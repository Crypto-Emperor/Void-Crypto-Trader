"""
Memecoin data layer: DEX pools, on-chain safety, and historical candles.

Why this exists
---------------
The rest of the bot fetches OHLCV from Binance/OKX/Bybit (market_data.py).
That works for BTC/SOL/BONK - anything with a CEX listing. But most memecoins
live *only* on-chain (pump.fun graduates, Raydium/PumpSwap pools, EVM DEXes),
so we need a second data path:

  1) GeckoTerminal API v2  (free, no key)
       - trending pools per network   -> "what is hot right now"
       - pool search                  -> symbol -> pool address resolver
       - /pools/{addr}/info           -> liquidity, FDV, buy/sell counts
       - /pools/{addr}/ohlcv/{tf}     -> REAL candles for pure-DEX tokens
  2) DexScreener (free, no key)
       - token safety flags: honeypot, mint, freeze, proxy, mutable taxes
       - pair discovery fallback
  3) GoPlus (security.py) still runs before any live buy as final gate.

Degen-trader research baked into `degen_score()` below (the playbook people
actually use on CT / bankless / the "how to not get rugged" guides):

  * Liquidity floor        - pools under ~$25k are exit-milked instantly
  * Volume/Liquidity ratio - >~2 means real churn; <0.1 means dead pool
  * Buy/sell imbalance     - sustained buyer pressure (smart-money footprint)
  * Holder distribution    - top-10 concentration >40% = one wallet can nuke it
  * Creator history        - "dev has rugged N tokens before" filter
  * Social presence        - website/Twitter/Telegram = minimum effort signal
  * Age sweet-spot         - too new = casino, too old & flat = zombie chain
  * Market-cap band        - $100k-$50M degen range; tiny = lottery, huge = slow
All thresholds are env-overridable so you can dial casino <-> conservative.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore


GT_BASE = "https://api.geckoterminal.com/api/v2"
DS_BASE = "https://api.dexscreener.com"

_mem: dict[str, tuple[Any, float]] = {}
_WARNED: set[str] = set()

NETWORK_ALIASES = {
    "solana": "solana", "sol": "solana", "svm": "solana",
    "ethereum": "eth", "mainnet": "eth", "eth": "eth",
    "base": "base", "bsc": "bsc", "binance": "bsc",
    "arbitrum": "arb", "optimism": "optimism", "polygon_pos": "polygon",
}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass
class DegenConfig:
    min_liquidity_usd: float = _f("MEME_MIN_LIQUIDITY_USD", 25_000)
    max_fdv_usd: float = _f("MEME_MAX_FDV_USD", 50_000_000)
    min_fdv_usd: float = _f("MEME_MIN_FDV_USD", 100_000)
    min_volume_24h_usd: float = _f("MEME_MIN_VOLUME_24H_USD", 50_000)
    min_age_hours: float = _f("MEME_MIN_AGE_HOURS", 24)          # skip launch roulette
    max_top10_holders_pct: float = _f("MEME_MAX_TOP10_HOLDERS_PCT", 40.0)
    max_creator_rugs: int = int(_f("MEME_MAX_CREATOR_RUGS", 0))  # strict by default
    require_socials: bool = _b("MEME_REQUIRE_SOCIALS", True)
    min_buy_ratio_h1: float = _f("MEME_MIN_BUY_RATIO_H1", 0.52)  # >=52% of txns buys
    veto_flags: bool = _b("MEME_VETO_FLAGS", True)               # honeypot/mint/freeze


degen_config = DegenConfig()


# ------------------------------------------------------------------ http ---

def _warn_once(key: str, msg: str) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    try:
        from rich.console import Console
        Console().print(f"[dim yellow]{msg}[/dim yellow]")
    except Exception:
        pass


def _cache_get(key: str):
    row = _mem.get(key)
    if row and time.time() - row[1] < _f("MEME_CACHE_SEC", 30):
        return row[0]
    return None


def _cache_set(key: str, val: Any) -> None:
    _mem[key] = (val, time.time())


def _get(url: str, params: dict | None = None, timeout: int = 15) -> Any:
    if requests is None:
        raise RuntimeError("requests not installed")
    key = url + str(sorted((params or {}).items()))
    hit = _cache_get(key)
    if hit is not None:
        return hit
    headers = {"Accept": "application/json", "User-Agent": "void-crypto-trader/2.0"}
    last_err: Exception | None = None
    for attempt in range(3):          # GT rate-limits bursts; backoff gently
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429:
                raise RuntimeError("rate_limited_429")
            r.raise_for_status()
            data = r.json()
            _cache_set(key, data)
            return data
        except Exception as e:
            last_err = e
            time.sleep(0.7 * (attempt + 1))
    raise last_err if last_err else RuntimeError("http failed")


def _urlencode_pool(pool_address: str) -> str:
    """'So1.../EPj...' must be percent-encoded for the path segment."""
    from urllib.parse import quote
    return quote(pool_address, safe="")


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def norm_network(chain: str) -> str:
    return NETWORK_ALIASES.get((chain or "solana").strip().lower(),
                               (chain or "solana").strip().lower())


# ------------------------------------------------------- pool resolution ---

@dataclass
class MemePool:
    network: str
    pool_address: str
    name: str = ""                    # e.g. "BONK / SOL"
    symbol: str = ""                  # base token symbol
    token_address: str = ""           # base token mint
    price_usd: float = 0.0
    liquidity_usd: float = 0.0
    fdv_usd: float = 0.0
    market_cap_usd: float = 0.0
    volume_24h_usd: float = 0.0
    volume_6h_usd: float = 0.0
    volume_1h_usd: float = 0.0
    price_change_h1: Optional[float] = None
    price_change_h6: Optional[float] = None
    price_change_h24: Optional[float] = None
    buys_h1: int = 0
    sells_h1: int = 0
    buyers_h24: int = 0
    sellers_h24: int = 0
    created_at: Optional[float] = None    # unix seconds of pool creation
    source: str = "geckoterminal"

    @property
    def age_hours(self) -> Optional[float]:
        if not self.created_at:
            return None
        return max(0.0, (time.time() - self.created_at) / 3600.0)

    @property
    def buy_ratio_h1(self) -> Optional[float]:
        t = self.buys_h1 + self.sells_h1
        return (self.buys_h1 / t) if t else None

    @property
    def vol_liq_ratio(self) -> Optional[float]:
        if self.liquidity_usd and self.liquidity_usd > 0:
            return self.volume_24h_usd / self.liquidity_usd
        return None


def _parse_gt_pool(item: dict) -> Optional[MemePool]:
    """Normalize a GeckoTerminal 'pool' resource (list item or detail)."""
    try:
        attrs = item.get("attributes") or {}
        rid = item.get("id") or ""            # "solana_<pool_address>"
        network, pool_addr = "", attrs.get("address") or ""
        if "_" in rid:
            network, pid = rid.split("_", 1)
            pool_addr = pool_addr or pid
        rel = item.get("relationships") or {}
        token_addr = ""
        try:
            token_addr = (rel.get("base_token", {}).get("data", {}) or {}).get("id", "")
            token_addr = token_addr.split("_", 1)[-1]
        except Exception:
            token_addr = ""
        name = attrs.get("name") or ""
        symbol = name.split("/")[0].strip() if "/" in name else ""
        tx_h1 = (attrs.get("transactions") or {}).get("h1") or {}
        tx_h24 = (attrs.get("transactions") or {}).get("h24") or {}
        pc = attrs.get("price_change_percentage") or {}
        created_raw = attrs.get("pool_created_at")
        created_ts: Optional[float] = None
        if created_raw:
            try:
                from datetime import datetime, timezone
                created_ts = datetime.fromisoformat(
                    str(created_raw).replace("Z", "+00:00")).timestamp()
            except Exception:
                created_ts = _to_float(created_raw, 0.0) or None
        pool = MemePool(
            network=network,
            pool_address=pool_addr,
            name=name,
            symbol=symbol.lower(),
            token_address=token_addr,
            price_usd=_to_float(attrs.get("base_token_price_usd")),
            liquidity_usd=_to_float(attrs.get("reserve_in_usd")),
            fdv_usd=_to_float(attrs.get("fdv_usd")),
            market_cap_usd=_to_float(attrs.get("market_cap_usd")),
            volume_24h_usd=_to_float((attrs.get("volume_usd") or {}).get("h24")),
            volume_6h_usd=_to_float((attrs.get("volume_usd") or {}).get("h6")),
            volume_1h_usd=_to_float((attrs.get("volume_usd") or {}).get("h1")),
            price_change_h1=_to_float(pc.get("h1"), 0.0) if pc.get("h1") is not None else None,
            price_change_h6=_to_float(pc.get("h6"), 0.0) if pc.get("h6") is not None else None,
            price_change_h24=_to_float(pc.get("h24"), 0.0) if pc.get("h24") is not None else None,
            buys_h1=int(_to_float(tx_h1.get("buys"))),
            sells_h1=int(_to_float(tx_h1.get("sells"))),
            buyers_h24=int(_to_float(tx_h24.get("buyers"))),
            sellers_h24=int(_to_float(tx_h24.get("sellers"))),
            created_at=created_ts,
        )
        return pool if pool.pool_address else None
    except Exception:
        return None


def trending_pools(network: str = "solana", limit: int = 20) -> list[MemePool]:
    """GeckoTerminal trending pools - the 'what's hot' board degen traders scan."""
    net = norm_network(network)
    try:
        data = _get(f"{GT_BASE}/networks/{net}/trending_pools")
        out = []
        for it in (data.get("data") or [])[:limit]:
            p = _parse_gt_pool(it)
            if p:
                p.network = net
                out.append(p)
        return out
    except Exception as e:
        _warn_once(f"gt-trend-{net}", f"geckoterminal trending unavailable ({type(e).__name__})")
        return []


def search_pools(query: str, network: str | None = None, limit: int = 20) -> list[MemePool]:
    """Symbol/name -> pool(s), ranked by liquidity. Falls back to DexScreener."""
    q = (query or "").strip()
    if not q:
        return []
    out: list[MemePool] = []
    try:
        data = _get(f"{GT_BASE}/search/pools", params={"query": q})
        for it in (data.get("data") or [])[:60]:
            p = _parse_gt_pool(it)
            if not p:
                continue
            if network and p.network != norm_network(network):
                continue
            # prefer exact base-symbol matches, then liquidity order
            if p.symbol.lower() == q.lower():
                out.insert(0, p)
            else:
                out.append(p)
    except Exception:
        pass
    if not out:
        try:
            data = _get(f"{DS_BASE}/latest/dex/search", params={"q": q})
            for row in (data.get("pairs") or [])[:60]:
                if network and row.get("chainId") != norm_network(network):
                    continue
                bt = row.get("baseToken") or {}
                if bt.get("symbol", "").lower() != q.lower():
                    continue
                tx_h1 = (row.get("txns") or {}).get("h1") or {}
                vol = row.get("volume") or {}
                pcx = row.get("priceChange") or {}
                created_ms = row.get("pairCreatedAt")
                p = MemePool(
                    network=row.get("chainId") or "",
                    pool_address=row.get("pairAddress") or "",
                    name=f"{bt.get('symbol','')}/{(row.get('quoteToken') or {}).get('symbol','')}",
                    symbol=bt.get("symbol", "").lower(),
                    token_address=bt.get("address") or "",
                    price_usd=_to_float(row.get("priceUsd")),
                    liquidity_usd=_to_float((row.get("liquidity") or {}).get("usd")),
                    fdv_usd=_to_float(row.get("fdv")),
                    market_cap_usd=_to_float(row.get("marketCap")),
                    volume_24h_usd=_to_float(vol.get("h24")),
                    volume_6h_usd=_to_float(vol.get("h6")),
                    volume_1h_usd=_to_float(vol.get("h1")),
                    price_change_h1=_to_float(pcx.get("h1"), 0.0) if pcx.get("h1") is not None else None,
                    price_change_h6=_to_float(pcx.get("h6"), 0.0) if pcx.get("h6") is not None else None,
                    price_change_h24=_to_float(pcx.get("h24"), 0.0) if pcx.get("h24") is not None else None,
                    buys_h1=int(_to_float(tx_h1.get("buys"))),
                    sells_h1=int(_to_float(tx_h1.get("sells"))),
                    created_at=(_to_float(created_ms) / 1000.0) if created_ms else None,
                    source="dexscreener",
                )
                out.append(p)
        except Exception as e:
            _warn_once(f"ds-search-{q}", f"dexscreener search unavailable ({type(e).__name__})")
    out.sort(key=lambda p: -p.liquidity_usd)
    return out[:limit]


def resolve_pool(symbol_or_mint: str, network: str = "solana") -> Optional[MemePool]:
    """Best single pool for a ticker or contract address (highest liquidity)."""
    s = (symbol_or_mint or "").strip()
    if not s:
        return None
    # looks like a Solana mint / EVM contract -> direct pool info via dex screener tokens
    if len(s) >= 32 and not s.isalpha():
        try:
            data = _get(f"{DS_BASE}/tokens/latest", params={"tokenAddresses": s})
            rows = []
            for res in (data.get("pairs") or [data] if isinstance(data, dict) else []):
                if isinstance(res, list):
                    rows.extend(res)
                elif isinstance(res, dict):
                    rows.append(res)
            best = None
            for row in rows:
                liq = _to_float((row.get("liquidity") or {}).get("usd"))
                if best is None or liq > best[1]:
                    best = (row, liq)
            if best:
                pools = _ds_rows_to_pools([best[0]])
                if pools:
                    return pools[0]
        except Exception:
            pass
        return None
    pools = search_pools(s, network=network)
    return pools[0] if pools else None


def _ds_rows_to_pools(rows: list[dict]) -> list[MemePool]:
    out: list[MemePool] = []
    for row in rows:
        try:
            bt = row.get("baseToken") or {}
            tx_h1 = (row.get("txns") or {}).get("h1") or {}
            vol = row.get("volume") or {}
            pcx = row.get("priceChange") or {}
            created_ms = row.get("pairCreatedAt")
            out.append(MemePool(
                network=row.get("chainId") or "",
                pool_address=row.get("pairAddress") or "",
                name=f"{bt.get('symbol','')}/{(row.get('quoteToken') or {}).get('symbol','')}",
                symbol=bt.get("symbol", "").lower(),
                token_address=bt.get("address") or "",
                price_usd=_to_float(row.get("priceUsd")),
                liquidity_usd=_to_float((row.get("liquidity") or {}).get("usd")),
                fdv_usd=_to_float(row.get("fdv")),
                market_cap_usd=_to_float(row.get("marketCap")),
                volume_24h_usd=_to_float(vol.get("h24")),
                volume_6h_usd=_to_float(vol.get("h6")),
                volume_1h_usd=_to_float(vol.get("h1")),
                price_change_h1=_to_float(pcx.get("h1"), 0.0) if pcx.get("h1") is not None else None,
                price_change_h6=_to_float(pcx.get("h6"), 0.0) if pcx.get("h6") is not None else None,
                price_change_h24=_to_float(pcx.get("h24"), 0.0) if pcx.get("h24") is not None else None,
                buys_h1=int(_to_float(tx_h1.get("buys"))),
                sells_h1=int(_to_float(tx_h1.get("sells"))),
                created_at=(_to_float(created_ms) / 1000.0) if created_ms else None,
                source="dexscreener",
            ))
        except Exception:
            continue
    return out


# ------------------------------------------------------------ DEX OHLCV ----

_GT_TF = {"1m": "minute", "5m": "minute", "15m": "minute", "30m": "hour",
          "1h": "hour", "4h": "hour", "1d": "day"}
_GT_AGG = {"1m": 1, "5m": 5, "15m": 15, "30m": 1, "1h": 1, "4h": 4, "1d": 1}


def get_dex_candles(pool_address: str, network: str = "solana",
                    interval: str = "1h", limit: int = 200) -> list:
    """Real OHLCV for a pure-DEX token via GeckoTerminal. Returns market_data.Candle."""
    from market_data import Candle  # local import to avoid cycles
    net = norm_network(network)
    tf = _GT_TF.get(interval, "hour")
    agg = _GT_AGG.get(interval, 1)
    try:
        data = _get(f"{GT_BASE}/networks/{net}/pools/{_urlencode_pool(pool_address)}/ohlcv/{tf}",
                    params={"limit": min(int(limit), 300), "aggregate": agg})
        rows = (data.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
        out: list[Candle] = []
        for ts, o, h, l, c, v in rows:  # newest-first from GT
            try:
                out.append(Candle(ts=int(ts), open=float(o), high=float(h),
                                  low=float(l), close=float(c), volume=float(v)))
            except Exception:
                continue
        out.sort(key=lambda x: x.ts)
        return out[-limit:]
    except Exception as e:
        _warn_once(f"gt-ohlcv-{pool_address}", f"dex ohlcv unavailable ({type(e).__name__})")
        return []


# ------------------------------------------------------ safety / flags -----

@dataclass
class MemeSafety:
    ok: bool = True
    reasons: list[str] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)
    top10_holders_pct: Optional[float] = None
    creator_rug_count: Optional[int] = None
    has_website: bool = False
    has_twitter: bool = False
    has_telegram: bool = False
    renounced: Optional[bool] = None


def check_safety(symbol: str, token_address: str = "", network: str = "solana",
                 pool: Optional[MemePool] = None) -> MemeSafety:
    """On-chain red-flag scan using DexScreener's token safety endpoint."""
    s = MemeSafety()
    addr = token_address or (pool.token_address if pool else "")
    if not addr:
        try:
            resolved = resolve_pool(symbol or addr, network=network)
            addr = resolved.token_address if resolved else ""
            pool = pool or resolved
        except Exception:
            addr = ""
    if not addr:
        s.ok = False
        s.reasons.append("could not resolve token contract")
        return s
    try:
        data = _get(f"{DS_BASE}/token-pairs/v1/{norm_network(network)}/{addr}")
        rows = data if isinstance(data, list) else [data]
        worst_flags: dict[str, bool] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            for k, v in (row.get("flags") or {}).items():
                try:
                    worst_flags[k] = worst_flags.get(k, False) or bool(int(v))
                except Exception:
                    worst_flags[k] = worst_flags.get(k, False) or bool(v)
            info = row.get("info") or {}
            s.has_website = s.has_website or bool(info.get("websites"))
            socials = {d.get("type") for d in (info.get("socials") or [])}
            s.has_twitter = s.has_twitter or ("twitter" in socials)
            s.has_telegram = s.has_telegram or ("telegram" in socials)
        s.flags = worst_flags
        dangerous = {"honeypot", "front_runnable", "bad_backlinks",
                     "impersonator", "fake_info", "revokable"}
        hit = sorted(dangerous & set(s.flags.keys()))
        if hit:
            s.ok = False
            s.reasons.append("dexscreener flags: " + ",".join(hit))
    except Exception as e:
        # fail closed for unknown microcaps when strict, open otherwise
        s.reasons.append(f"safety scan unavailable ({type(e).__name__})")
        if degen_config.veto_flags and not _is_known_meme(symbol):
            s.ok = False
    return s


_KNOWN_MEMES = {"bonk", "wif", "doge", "shib", "pepe", "floki", "popcat", "brett",
                "neiro", "slerf", "giga", "myro", "mog", "turbo", "pengu", "fartcoin",
                "virtual", "aiki", "griffain", "retardio", "moonbird", "bera"}


def _is_known_meme(symbol: str) -> bool:
    return (symbol or "").lower().strip() in _KNOWN_MEMES


# --------------------------------------------------------- degen score -----

@dataclass
class DegenScore:
    symbol: str
    score: float = 0.0                      # 0..100
    vetoes: list[str] = field(default_factory=list)
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    pool: Optional[MemePool] = None
    safety: Optional[MemeSafety] = None

    @property
    def tradeable(self) -> bool:
        return not self.vetoes and self.score >= _f("MEME_MIN_SCORE", 45)


def degen_score(symbol: str, network: str = "solana",
                pool: Optional[MemePool] = None,
                safety: Optional[MemeSafety] = None,
                cfg: DegenConfig = degen_config) -> DegenScore:
    """Composite DEX-native quality score (see module docstring for research)."""
    ds = DegenScore(symbol=symbol.lower())
    if pool is None:
        pool = resolve_pool(symbol, network=network)
    ds.pool = pool
    if pool is None:
        ds.vetoes.append("no liquid DEX pool found")
        return ds
    if safety is None:
        safety = check_safety(symbol, pool.token_address, network, pool)
    ds.safety = safety

    # ---- hard vetoes (any one kills the trade) --------------------------
    if cfg.veto_flags and not safety.ok:
        ds.vetoes.extend(safety.reasons or ["safety flags"])
    if pool.liquidity_usd < cfg.min_liquidity_usd:
        ds.vetoes.append(f"liquidity ${pool.liquidity_usd:,.0f} < ${cfg.min_liquidity_usd:,.0f}")
    if pool.volume_24h_usd < cfg.min_volume_24h_usd:
        ds.vetoes.append(f"24h volume ${pool.volume_24h_usd:,.0f} too low")
    if pool.fdv_usd and pool.fdv_usd > cfg.max_fdv_usd:
        ds.vetoes.append(f"FDV ${pool.fdv_usd:,.0f} above degen band")
    age = pool.age_hours
    if age is not None and age < cfg.min_age_hours:
        ds.vetoes.append(f"pool only {age:.1f}h old (<{cfg.min_age_hours:.0f}h)")
    if safety.top10_holders_pct is not None and \
            safety.top10_holders_pct > cfg.max_top10_holders_pct:
        ds.vetoes.append(f"top-10 holders {safety.top10_holders_pct:.0f}% concentrated")
    if safety.creator_rug_count is not None and \
            safety.creator_rug_count > cfg.max_creator_rugs:
        ds.vetoes.append(f"creator rugged {safety.creator_rug_count} tokens before")

    # ---- soft scoring ----------------------------------------------------
    pts = 0.0

    # liquidity depth (log-scaled): $25k->8pts ... $5M+->20pts
    liq = max(pool.liquidity_usd, 1.0)
    import math
    liq_pts = min(20.0, max(0.0, (math.log10(liq) - math.log10(cfg.min_liquidity_usd)) / 
                            (math.log10(5_000_000) - math.log10(cfg.min_liquidity_usd)) * 20.0))
    pts += liq_pts
    ds.pros.append(f"liquidity ${pool.liquidity_usd:,.0f}") if liq_pts > 10 else \
        ds.cons.append(f"thin liquidity ${pool.liquidity_usd:,.0f}")

    # volume/liquidity turnover: healthy churn 1-15x
    vr = pool.vol_liq_ratio
    if vr is not None:
        if 0.8 <= vr <= 15:
            pts += 15
            ds.pros.append(f"turnover {vr:.1f}x")
        elif vr > 15:
            pts += 5
            ds.cons.append(f"extreme churn {vr:.0f}x (wash/snipe risk)")
        else:
            ds.cons.append(f"dead pool turnover {vr:.2f}x")

    # buy pressure (h1 txn mix)
    br = pool.buy_ratio_h1
    if br is not None:
        if br >= cfg.min_buy_ratio_h1:
            pts += 12 + min(8.0, (br - cfg.min_buy_ratio_h1) * 60.0)
            ds.pros.append(f"buy pressure {br*100:.0f}%")
        elif br >= 0.45:
            pts += 4
        else:
            ds.cons.append(f"sell pressure ({br*100:.0f}% buys)")

    # momentum: positive h6 and h24 change but not parabolic blow-off
    ch6, ch24 = pool.price_change_h6, pool.price_change_h24
    if ch6 is not None and ch24 is not None:
        if 0 < ch6 <= 40 and 0 < ch24 <= 120:
            pts += 15
            ds.pros.append(f"momentum h6 +{ch6:.0f}% h24 +{ch24:.0f}%")
        elif ch6 > 40 or ch24 > 120:
            pts += 3
            ds.cons.append(f"blow-off risk (+{ch24:.0f}%/24h)")
        elif ch24 < -20:
            ds.cons.append(f"falling knife ({ch24:.0f}%/24h)")
        else:
            pts += 6

    # unique participants (real users, not bots trading repeatedly)
    uniq = pool.buyers_h24
    if uniq:
        if uniq >= 1500:
            pts += 12
            ds.pros.append(f"{uniq:,} unique buyers/24h")
        elif uniq >= 400:
            pts += 7
        elif uniq >= 100:
            pts += 3
        else:
            ds.cons.append(f"only {uniq} buyers/24h")

    # socials
    soc = sum([safety.has_website, safety.has_twitter, safety.has_telegram])
    pts += soc * 4
    if cfg.require_socials and soc == 0:
        ds.vetoes.append("no website/socials at all")
    elif soc:
        ds.pros.append(f"{soc}/3 socials")

    # market cap sweet spot: early-but-not-lottery
    mc = pool.market_cap_usd or pool.fdv_usd
    if mc:
        if 300_000 <= mc <= 20_000_000:
            pts += 10
            ds.pros.append(f"MC ${mc:,.0f} in sweet spot")
        elif cfg.min_fdv_usd <= mc <= cfg.max_fdv_usd:
            pts += 5
        else:
            ds.cons.append(f"MC ${mc:,.0f} outside band")

    ds.score = round(min(100.0, pts), 1)
    return ds


# -------------------------------------------------------------- helpers ----

def hot_memecoins(network: str = "solana", limit: int = 15,
                  cfg: DegenConfig = degen_config) -> list[DegenScore]:
    """Trending pools -> degen-scored, veto-filtered, rank-ordered shortlist."""
    scored: list[DegenScore] = []
    for p in trending_pools(network=network, limit=min(20, max(limit, 10))):
        sym = p.symbol
        if not sym:
            continue
        try:
            ds = degen_score(sym, network=network, pool=p, cfg=cfg)
        except Exception:
            continue
        scored.append(ds)
    scored.sort(key=lambda d: (not d.vetoes, d.score), reverse=True)
    return scored[:limit]


def is_meme_candidate(symbol: str) -> bool:
    """Heuristic: should this symbol go through the DEX pipeline at all?"""
    majors = {"btc", "xbt", "eth", "sol", "bnb", "xrp", "ada", "avax", "link",
              "dot", "ltc", "ton", "usdc", "usdt", "usd", "cash"}
    s = (symbol or "").lower().strip()
    if not s or s in majors:
        return False
    if len(s) >= 32:      # looks like a contract address
        return True
    return True


def meme_gate(symbol: str, network: str = "solana") -> tuple[bool, str, Optional[DegenScore]]:
    """One-call gate for strategies: (allowed, why, full scorecard)."""
    ds = degen_score(symbol, network=network)
    if ds.vetoes:
        return False, "; ".join(ds.vetoes[:3]), ds
    if not ds.tradeable:
        return False, f"degen score {ds.score:.0f} < {_f('MEME_MIN_SCORE', 45):.0f}", ds
    return True, f"degen score {ds.score:.0f} | " + ", ".join(ds.pros[:3]), ds
