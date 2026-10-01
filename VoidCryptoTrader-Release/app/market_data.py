"""
Real OHLCV candle history from free public exchange REST APIs.

Why this exists: every serious crypto trader (trend followers, swing traders,
quant desks) sizes entries and stops off *structure*, not single price ticks:
  - EMA crossovers (Turtle / "Golden Cross" style trend filters)
  - ATR-based stops and targets (Van Tharp / Chandelier exits)
  - RSI / MACD momentum confirmation
  - realized volatility for position sizing (vol targeting)
  - volume spikes vs their own baseline (smart-money flow proxy)

All of that needs candles. `prices.py` only gives a spot tick and
`predict.py` keeps ~50 samples in a JSON file, so we fetch proper history here.

Sources (tried in order, all free, no API key):
  1) Binance   /api/v3/klines      (best depth + volume quality)
  2) OKX       /api/v5/market/candles
  3) Bybit     /v5/market/kline

Results are cached on disk (JSONL per symbol+interval) so repeated scans are
free and the bot still works offline.
"""

from __future__ import annotations

import json
import os
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

from config import BASE_DIR

CACHE_DIR = Path(os.getenv("OHLCV_CACHE_DIR", str(BASE_DIR / "data" / "ohlcv")))
_LOCK = threading.Lock()

# interval -> (binance, okx, bybit) native names
INTERVALS: dict[str, tuple[str, str, str]] = {
    "1m": ("1m", "1m", "1"),
    "5m": ("5m", "5m", "5"),
    "15m": ("15m", "15m", "15"),
    "30m": ("30m", "30m", "30"),
    "1h": ("1h", "1H", "60"),
    "4h": ("4h", "4H", "240"),
    "1d": ("1d", "1D", "D"),
}

# well-known quote aliases -> Binance/OKX/Bybit symbols
SYMBOL_ALIASES = {
    "btc": "BTCUSDT",
    "xbt": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "bnb": "BNBUSDT",
    "xrp": "XRPUSDT",
    "doge": "DOGEUSDT",
    "ada": "ADAUSDT",
    "avax": "AVAXUSDT",
    "link": "LINKUSDT",
    "tnt": "TNTUSDT",
    "bonk": "BONKUSDT",
    "wif": "WIFUSDT",
    "jup": "JUPUSDT",
    "ray": "RAYUSDT",
    "pyth": "PYTHUSDT",
    "render": "RENDERUSDT",
    "rndr": "RENDERUSDT",
    "jto": "JTOUSDT",
    "popcat": "POPCATUSDT",
    "neiro": "NEIROUSDT",
}


@dataclass
class Candle:
    ts: int      # unix seconds (candle open time)
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def range(self) -> float:
        return max(self.high - self.low, 1e-12)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)


def _now_sec() -> float:
    return time.time()


def to_exchange_symbol(symbol: str) -> str:
    """'sol' -> 'SOLUSDT'. Already-paired symbols pass through."""
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    if s.endswith("USDT") or s.endswith("USDC"):
        return s
    if s in ("USD", "USDC", "USDT", "CASH"):
        return "USDCUSDT"
    return SYMBOL_ALIASES.get(s.lower(), f"{s}USDT")


def _cache_path(symbol: str, interval: str) -> Path:
    safe = "".join(c for c in symbol.upper() if c.isalnum()) or "NA"
    return CACHE_DIR / f"{safe}_{interval}.jsonl"


def _load_cache(path: Path, max_age_sec: float) -> list[Candle]:
    if not path.exists():
        return []
    try:
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                rows.append(Candle(**d))
            except Exception:
                continue
        if not rows:
            return []
        fresh_enough = (_now_sec() - rows[-1].ts) < max_age_sec
        return rows if fresh_enough else rows
    except Exception:
        return []


def _save_cache(path: Path, candles: list[Candle], keep: int = 1000) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(
            json.dumps({
                "ts": c.ts, "open": c.open, "high": c.high,
                "low": c.low, "close": c.close, "volume": c.volume,
            })
            for c in candles[-keep:]
        )
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _http_get(url: str, params: dict | None = None, timeout: int = 12):
    if requests is None:
        raise RuntimeError("requests not installed")
    headers = {"Accept": "application/json", "User-Agent": "void-crypto-trader/2.0"}
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    if r.status_code in (418, 429):
        raise RuntimeError(f"rate_limited_{r.status_code}")
    r.raise_for_status()
    return r.json()


# ---------------- providers ----------------

def _binance_klines(ex_sym: str, interval: str, limit: int) -> list[Candle]:
    bi = INTERVALS.get(interval, (interval,))[0]
    data = _http_get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": ex_sym, "interval": bi, "limit": max(1, min(limit, 1000))},
    )
    out: list[Candle] = []
    for row in data:
        try:
            out.append(Candle(
                ts=int(row[0]) // 1000,
                open=float(row[1]), high=float(row[2]),
                low=float(row[3]), close=float(row[4]),
                volume=float(row[5]),
            ))
        except Exception:
            continue
    return out


def _okx_klines(ex_sym: str, interval: str, limit: int) -> list[Candle]:
    oi = INTERVALS.get(interval, (None, interval, None))[1]
    data = _http_get(
        "https://www.okx.com/api/v5/market/candles",
        params={"instId": ex_sym.replace("USDT", "-USDT"), "bar": oi,
                "limit": max(1, min(limit, 300))},
    )
    rows = (data or {}).get("data") or []
    out: list[Candle] = []
    for row in reversed(rows):  # okx returns newest first
        try:
            out.append(Candle(
                ts=int(row[0]) // 1000,
                open=float(row[1]), high=float(row[2]),
                low=float(row[3]), close=float(row[4]),
                volume=float(row[5]),
            ))
        except Exception:
            continue
    return out


def _bybit_klines(ex_sym: str, interval: str, limit: int) -> list[Candle]:
    byi = INTERVALS.get(interval, (None, None, interval))[2]
    data = _http_get(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "linear", "symbol": ex_sym, "interval": byi,
                "limit": max(1, min(limit, 1000))},
    )
    rows = ((data or {}).get("result") or {}).get("list") or []
    out: list[Candle] = []
    for row in reversed(rows):  # bybit returns newest first
        try:
            out.append(Candle(
                ts=int(row[0]) // 1000,
                open=float(row[1]), high=float(row[2]),
                low=float(row[3]), close=float(row[4]),
                volume=float(row[5]),
            ))
        except Exception:
            continue
    return out


_PROVIDERS = (
    ("binance", _binance_klines),
    ("okx", _okx_klines),
    ("bybit", _bybit_klines),
)

_MEM: dict[str, tuple[float, list[Candle]]] = {}
MEM_TTL = float(os.getenv("OHLCV_MEM_TTL", "45"))


# Tokens that live (mostly) on-chain: try CEX klines first, fall back to
# GeckoTerminal DEX pool OHLCV so memecoin strategies get *real* candles.
_DEX_FIRST_HINTS = {"pump", "bonkfun", "raydium"}


def _dex_fallback_candles(symbol: str, interval: str, limit: int) -> tuple[list[Candle], str]:
    """Resolve a meme token to its deepest DEX pool and pull GT candles."""
    try:
        import memecoin as mm
        pool = mm.resolve_pool(symbol, network=os.getenv("MEME_DEFAULT_NETWORK", "solana"))
        if pool is None or not pool.pool_address:
            return [], ""
        cs = mm.get_dex_candles(pool.pool_address, network=pool.network or "solana",
                                interval=interval, limit=limit)
        if len(cs) >= 3:
            # cache under the synthetic exchange symbol too
            _save_cache(_cache_path(to_exchange_symbol(symbol), interval), cs)
            return cs, "geckoterminal-dex"
    except Exception:
        pass
    return [], ""


def get_candles(
    symbol: str,
    interval: str = "1h",
    limit: int = 200,
    use_network: bool = True,
) -> list[Candle]:
    """Return oldest->newest candles for symbol/interval. Never raises.

    Order: CEX klines (Binance/OKX/Bybit) -> DEX pools via GeckoTerminal
    (memecoins without a CEX listing) -> disk cache -> spot-tick backstop.
    """
    interval = interval.lower()
    if interval not in INTERVALS:
        interval = "1h"
    ex_sym = to_exchange_symbol(symbol)
    if not ex_sym:
        return []
    key = f"{ex_sym}:{interval}:{limit}"

    hit = _MEM.get(key)
    if hit and (_now_sec() - hit[0]) < MEM_TTL:
        return hit[1]

    path = _cache_path(ex_sym, interval)
    candles: list[Candle] = []
    source = "cache"

    if use_network:
        err = ""
        for name, fn in _PROVIDERS:
            try:
                with _LOCK:
                    candles = fn(ex_sym, interval, limit)
                if len(candles) >= 3:
                    source = name
                    break
            except Exception as e:
                err += f"{name}:{type(e).__name__} "
                candles = []
        # no CEX listing -> this may be a pure-DEX memecoin; fetch pool candles
        if not candles:
            dex_cs, dex_src = _dex_fallback_candles(symbol, interval, limit)
            if dex_cs:
                candles, source = dex_cs, dex_src
        if candles:
            _save_cache(path, candles)
        else:
            if err:
                _warn_once(key, f"ohlcv fetch failed ({err.strip()[:120]}) - using cache")
            candles = _load_cache(path, max_age_sec=0) or []

    if not candles:
        candles = _load_cache(path, max_age_sec=0) or []

    # spot backstop: if we truly have nothing but the exchange has a live price
    if not candles and use_network:
        try:
            from prices import get_price
            px = get_price(symbol, "auto")
            if px:
                t = int(_now_sec())
                candles = [Candle(t, px, px, px, px, 0.0)]
                source = "spot"
        except Exception:
            pass

    candles = candles[-limit:] if candles else []
    _MEM[key] = (_now_sec(), candles)
    _SOURCE_STATS[source] = _SOURCE_STATS.get(source, 0) + 1
    return candles


_SOURCE_STATS: dict[str, int] = {}


_WARNED: set[str] = set()


def _warn_once(key: str, msg: str) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    try:
        from rich.console import Console
        Console().print(f"[dim yellow]{msg}[/dim yellow]")
    except Exception:
        pass


def closes(symbol: str, interval: str = "1h", limit: int = 200) -> list[float]:
    return [c.close for c in get_candles(symbol, interval, limit)]


def last_price_from_candles(symbol: str, interval: str = "1h") -> Optional[float]:
    cs = get_candles(symbol, interval, 5)
    return cs[-1].close if cs else None


def change_pct(symbol: str, interval: str = "1h", bars: int = 24) -> Optional[float]:
    """% change over the last `bars` candles of `interval` (real history)."""
    cs = get_candles(symbol, interval, bars + 2)
    if len(cs) < 2:
        return None
    a = cs[-1 - min(bars, len(cs) - 1)].close
    b = cs[-1].close
    if a <= 0:
        return None
    return (b - a) / a * 100.0
