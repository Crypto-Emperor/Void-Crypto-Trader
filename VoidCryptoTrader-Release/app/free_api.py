"""
Free multi-source market data (no fomoapi credits).

Sources (tried in order, resilient to rate limits / 402):
  1) CoinPaprika tickers (when available)
  2) CoinGecko trending
  3) DexScreener search / boosts
  4) Built-in major-token fallback so the bot never dies offline
"""

from __future__ import annotations
import os

try:
    from dotenv import load_dotenv
    from pathlib import Path as _P
    load_dotenv(_P(__file__).parent / ".env")
    load_dotenv(_P(__file__).parent / "keys" / "api_keys.env", override=True)
except Exception:
    pass

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from rich.console import Console


console = Console()
_mem: dict[str, tuple[Any, float]] = {}
TTL = int(os.getenv("FREE_API_CACHE_SEC", "15"))
UA = {"Accept": "application/json", "User-Agent": "void-crypto-trader/1.0"}
_WARNED: set[str] = set()


def _warn_once(key: str, msg: str) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        console.print(f"[dim yellow]{msg}[/dim yellow]")


def _cache_get(key: str):
    row = _mem.get(key)
    if row and time.time() - row[1] < TTL:
        return row[0]
    return None


def _cache_set(key: str, val: Any) -> None:
    _mem[key] = (val, time.time())


def _to_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        try:
            return float(val)
        except Exception:
            return default
    s = str(val).strip()
    # strip currency symbols / spaces / thin spaces
    for ch in ("$", "€", "£", "¥", "%", ",", " ", "\u00a0", "\u202f"):
        s = s.replace(ch, "")
    if not s or s.lower() in ("-", "n/a", "null", "none", "-"):
        return default
    m = re.match(r"^([+-]?\d*\.?\d+)([KMBTkmbt])?$", s)
    if m:
        try:
            n = float(m.group(1))
        except Exception:
            return default
        suf = (m.group(2) or "").upper()
        mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(suf, 1)
        return n * mult
    try:
        return float(s)
    except Exception:
        return default


def _get(url: str, timeout: int = 8) -> Any:
    from http_util import get_json
    try:
        return get_json(url, timeout=timeout, headers=UA, max_retries=1)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(str(e)) from e


# ---------- CoinPaprika ----------

def paprika_tickers(limit: int = 100) -> list[dict]:
    hit = _cache_get("paprika")
    if hit is not None:
        return hit[:limit]
    try:
        data = _get("https://api.coinpaprika.com/v1/tickers?quotes=USD")
        if not isinstance(data, list):
            return []
        skip = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "USDE", "USD1"}
        rows = []
        for t in data:
            sym = (t.get("symbol") or "").upper()
            if sym in skip:
                continue
            q = (t.get("quotes") or {}).get("USD") or {}
            vol = _to_float(q.get("volume_24h"))
            if vol < 50_000:
                continue
            rows.append(
                {
                    "symbol": sym.lower(),
                    "name": t.get("name") or sym,
                    "id": t.get("id"),
                    "price_usd": _to_float(q.get("price")),
                    "volume_h24": vol,
                    "change_1h": _to_float(q.get("percent_change_1h")),
                    "change_h24": _to_float(q.get("percent_change_24h")),
                    "change_7d": _to_float(q.get("percent_change_7d")),
                    "market_cap": _to_float(q.get("market_cap")),
                    "source": "paprika",
                }
            )
        rows.sort(key=lambda x: x["volume_h24"], reverse=True)
        _cache_set("paprika", rows)
        return rows[:limit]
    except Exception as e:
        _warn_once("paprika", f"paprika unavailable ({e}) - using other free sources")
        return []


def paprika_movers(limit: int = 30, min_vol: float = 200_000) -> list[dict]:
    rows = paprika_tickers(limit=500)
    movers = [r for r in rows if r["volume_h24"] >= min_vol and abs(r["change_h24"]) >= 1]
    movers.sort(key=lambda x: abs(x["change_h24"]) * (x["volume_h24"] ** 0.5), reverse=True)
    return movers[:limit]


# ---------- CoinGecko trending ----------

def coingecko_trending(limit: int = 15) -> list[dict]:
    hit = _cache_get("cg_trend")
    if hit is not None:
        return hit[:limit]
    try:
        data = _get("https://api.coingecko.com/api/v3/search/trending")
    except Exception as e:
        _warn_once("cg", f"coingecko trending unavailable ({e})")
        return []
    if not isinstance(data, dict):
        return []
    coins = data.get("coins") or []
    rows = []
    for c in coins[:limit]:
        try:
            item = (c or {}).get("item") or {}
            d = item.get("data") if isinstance(item.get("data"), dict) else {}
            pcp = d.get("price_change_percentage_24h") if d else None
            if isinstance(pcp, dict):
                ch = _to_float(pcp.get("usd"))
            else:
                ch = _to_float(pcp)
            rows.append(
                {
                    "symbol": str(item.get("symbol") or "?").lower(),
                    "name": item.get("name") or "",
                    "id": item.get("id"),
                    "price_usd": _to_float(d.get("price") if d else 0),
                    "volume_h24": _to_float(d.get("total_volume") if d else 0),
                    "change_h24": ch,
                    "change_1h": 0.0,
                    "source": "coingecko_trending",
                }
            )
        except Exception:
            continue
    _cache_set("cg_trend", rows)
    return rows


# ---------- DexScreener ----------

def dexscreener_search(q: str = "SOL", limit: int = 25) -> list[dict]:
    key = f"dx_{q}"
    hit = _cache_get(key)
    if hit is not None:
        return hit[:limit]
    try:
        data = _get(f"https://api.dexscreener.com/latest/dex/search?q={q}")
        if not isinstance(data, dict):
            return []
        pairs = data.get("pairs") or []
        out = []
        seen = set()
        for p in pairs:
            tok = p.get("baseToken") or {}
            sym = (tok.get("symbol") or "").lower()
            if not sym or sym in seen or sym in ("usdc", "usdt", "wsol"):
                continue
            seen.add(sym)
            out.append(
                {
                    "symbol": sym,
                    "address": tok.get("address"),
                    "price_usd": _to_float(p.get("priceUsd")),
                    "volume_h24": _to_float((p.get("volume") or {}).get("h24")),
                    "change_h24": _to_float((p.get("priceChange") or {}).get("h24")),
                    "change_1h": _to_float((p.get("priceChange") or {}).get("h1")),
                    "url": p.get("url"),
                    "chain": p.get("chainId"),
                    "source": "dexscreener",
                }
            )
            if len(out) >= limit:
                break
        _cache_set(key, out)
        return out
    except Exception as e:
        _warn_once("dx", f"dexscreener unavailable ({e})")
        return []



FOMO_BLOCKLIST = {
    "btc", "eth", "bnb", "xrp", "ada", "doge", "avax", "link", "dot",
    "near", "sui", "op", "arb", "matic", "usdt", "usdc", "usd", "dai",
    "wbtc", "steth", "shib",  # non-FOMO majors
}


def _fallback_majors() -> list[dict]:
    """
    FOMO-style Solana memecoin universe (offline-safe approx prices).
    Real prices used when network price fetch works.
    """
    px = {
        # base
        "sol": 150.0,
        # core FOMO-style memes
        "bonk": 0.00002,
        "wif": 1.5,
        "popcat": 0.50,
        "mew": 0.005,
        "bome": 0.01,
        "slerf": 0.20,
        "wen": 0.0001,
        "mother": 0.05,
        "trump": 10.0,
        "fartcoin": 0.50,
        "ai16z": 1.0,
        "goat": 0.50,
        "pnut": 0.30,
        "act": 0.20,
        "moodeng": 0.20,
        "giga": 0.05,
        "spx": 0.50,
        "micchi": 0.01,
        "retardio": 0.05,
        "chillguy": 0.10,
        "neiro": 0.001,
        "pepe": 0.00001,
        "mog": 0.000001,
        "andy": 0.05,
        "brett": 0.10,
        # solana infra still used on FOMO often
        "jup": 0.80,
        "ray": 3.0,
        "jto": 2.5,
        "pyth": 0.40,
        "render": 7.0,
        "w": 0.30,
        "io": 2.0,
        "tnsr": 0.40,
        "meme": 0.02,
        "hammy": 0.01,
        "lockin": 0.05,
        "sc": 0.02,
        "fwog": 0.05,
        "michi": 0.10,
        "ponke": 0.40,
        "myro": 0.10,
        "analos": 0.01,
        "smoog": 0.01,
        "nos": 0.05,
    }
    rows = []
    for sym, price in px.items():
        rows.append(
            {
                "symbol": sym,
                "price_usd": float(price),
                "volume_h24": 1_000_000.0,
                "change_h24": 2.0,
                "change_1h": 0.5,
                "source": "fallback",
                "chain": "solana",
            }
        )
    return rows



def free_hot_tokens(limit: int = 25) -> list[dict]:
    """
    Token universe for trading:
      1) FOMO API boards (trending + most-held + graduated) when FOMOAPI_KEY set
      2) else offline FOMO-style Solana meme fallback (NOT full FOMO catalog)
    """
    import os
    hit = _cache_get("hot_tokens")
    if hit is not None:
        return hit[:limit]

    rows: list[dict] = []
    # Real FOMO memecoins
    try:
        from fomo_tokens import has_fomo_key, fomo_universe_as_hot_rows
        if has_fomo_key():
            rows = fomo_universe_as_hot_rows(limit=max(limit, 60))
            if rows:
                _cache_set("hot_tokens", rows)
                return rows[:limit]
    except Exception:
        pass

    rows = list(_fallback_majors())
    if os.getenv("FREE_API_NETWORK", "1").lower() in ("1", "true", "yes"):
        try:
            seen = {(r.get("symbol") or "").lower() for r in rows}
            def add(batch):
                for r in batch:
                    sym = (r.get("symbol") or "").lower()
                    if not sym or sym in seen or sym in FOMO_BLOCKLIST:
                        continue
                    seen.add(sym)
                    rows.append(r)
            try:
                add(coingecko_trending(limit=15))
            except Exception:
                pass
            for q in ("SOL", "pump", "meme", "bonk"):
                try:
                    add(dexscreener_search(q, limit=12))
                except Exception:
                    pass
        except Exception:
            pass

    rows = [r for r in rows if (r.get("symbol") or "").lower() not in FOMO_BLOCKLIST]
    if not rows:
        rows = list(_fallback_majors())
    _cache_set("hot_tokens", rows)
    return rows[:limit]



def enriched_hot_tokens(limit: int = 15) -> list[dict]:
    return free_hot_tokens(limit=limit)


@dataclass
class FreeTrader:
    rank: int
    handle: str
    pnl_usd: float = 0.0
    volume_usd: float = 0.0
    trades: int = 0
    top_tokens: list[str] = field(default_factory=list)
    verified: bool = False


@dataclass
class FreeTrade:
    trade_id: str
    handle: str
    symbol: str
    mint: Optional[str]
    status: str
    side_hint: str
    usd_value: float
    created_at: str = ""


def free_leaderboard(window: str = "24h", limit: int = 15) -> list[FreeTrader]:
    hot = free_hot_tokens(limit=max(limit * 2, 20))
    traders: list[FreeTrader] = []
    for i, t in enumerate(hot[:limit]):
        ch = float(t.get("change_h24") or 0)
        vol = float(t.get("volume_h24") or 0)
        pnl = vol * (ch / 100.0) if ch else vol * 0.01
        sym = t.get("symbol") or f"t{i}"
        traders.append(
            FreeTrader(
                rank=i + 1,
                handle=f"mkt_{sym}"[:24],
                pnl_usd=pnl,
                volume_usd=vol,
                trades=int(max(1, vol / 50_000)),
                top_tokens=[sym],
                verified=t.get("source") in ("paprika", "dexscreener"),
            )
        )
    traders.sort(key=lambda x: x.pnl_usd, reverse=True)
    for i, tr in enumerate(traders, 1):
        tr.rank = i
    return traders


def free_trades_for_handle(handle: str, limit: int = 15) -> list[FreeTrade]:
    hot = free_hot_tokens(limit=40)
    h = handle.lower()
    bucket = sum(ord(c) for c in h) % 3
    out: list[FreeTrade] = []
    for i, t in enumerate(hot):
        if i % 3 != bucket:
            continue
        sym = (t.get("symbol") or "").lower()
        if not sym:
            continue
        ch1 = float(t.get("change_1h") or 0)
        ch24 = float(t.get("change_h24") or 0)
        vol = float(t.get("volume_h24") or 0)
        usd = min(max(vol * 0.0001, 25), 3000)
        mint = t.get("address")
        if ch1 >= 1.5 or ch24 >= 4:
            out.append(
                FreeTrade(
                    trade_id=f"free:{h}:{sym}:buy:{int(time.time()) // 120}",
                    handle=h,
                    symbol=sym,
                    mint=mint,
                    status="open",
                    side_hint="buy",
                    usd_value=usd,
                )
            )
        elif ch1 <= -3 or ch24 <= -8:
            out.append(
                FreeTrade(
                    trade_id=f"free:{h}:{sym}:sell:{int(time.time()) // 120}",
                    handle=h,
                    symbol=sym,
                    mint=mint,
                    status="closed",
                    side_hint="sell",
                    usd_value=usd,
                )
            )
        if len(out) >= limit:
            break
    if not out and hot:
        t = hot[0]
        sym = (t.get("symbol") or "sol").lower()
        out.append(
            FreeTrade(
                trade_id=f"free:{h}:{sym}:buy:fb",
                handle=h,
                symbol=sym,
                mint=t.get("address"),
                status="open",
                side_hint="buy",
                usd_value=100,
            )
        )
    return out


def dexscreener_solana_movers(limit: int = 25) -> list[dict]:
    rows = dexscreener_search("SOL", limit=limit)
    shaped = []
    for r in rows:
        shaped.append(
            {
                "baseToken": {"symbol": r["symbol"], "address": r.get("address")},
                "priceUsd": r.get("price_usd"),
                "volume": {"h24": r.get("volume_h24")},
                "priceChange": {"h24": r.get("change_h24"), "h1": r.get("change_1h")},
                "url": r.get("url"),
                "chainId": r.get("chain"),
            }
        )
    if not shaped:
        for t in free_hot_tokens(limit):
            shaped.append(
                {
                    "baseToken": {"symbol": t["symbol"], "address": t.get("address")},
                    "priceUsd": t.get("price_usd"),
                    "volume": {"h24": t.get("volume_h24")},
                    "priceChange": {"h24": t.get("change_h24"), "h1": t.get("change_1h")},
                }
            )
    return shaped


def data_source() -> str:
    forced = (os.getenv("DATA_SOURCE") or "").lower()
    if forced in ("free", "dex", "dexscreener", "paprika", "multi"):
        return "free"
    if forced in ("fomo", "fomoapi"):
        return "fomoapi"
    key = os.getenv("FOMOAPI_KEY") or os.getenv("FOMO_API_KEY")
    return "fomoapi" if key else "free"
