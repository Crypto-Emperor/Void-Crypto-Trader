"""
Dynamic pair (symbol) lists - inspired by freqtrade's PairList plugin system
(https://github.com/freqtrade/freqtrade/tree/develop/freqtrade/plugins/pairlist).

freqtrade's core idea: never trade a static whitelist. Continuously re-rank the
tradable universe by *liquidity* (VolumePairList), throw away pairs that are too
volatile for the account stake (VolatilityFilterPairList), drop stale pairs
(AgeFilter) and only keep what passes an entry signal (PerformanceFilter).

We implement the same plugin-chain concept with zero dependencies beyond our
own `market_data` module (free Binance/OKX/Bybit 24h ticker stats):

  VolumePairList   -> top-N tokens by 24h quote volume (liquidity first)
  AgeFilterPairList-> must have been listed >= N hours (no brand-new rugs)
  VolatilityFilter -> skip candles whose ATR% is outside [lo, hi] band
  PerformanceFilter-> only pairs whose confluence score crosses a threshold

Chain semantics match freqtrade: each handler receives the output of the
previous one; the first handler expands the universe, later ones filter it.
All results are TTL-cached so a fast tick loop doesn't hammer the exchange.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import BASE_DIR
from market_data import get_candles, to_exchange_symbol

CACHE_DIR = Path(os.getenv("PAIRLIST_CACHE_DIR", str(BASE_DIR / "data" / "pairlist")))
REFRESH_SEC = float(os.getenv("PAIRLIST_REFRESH_SEC", "1800"))  # freqtrade default: 1800


# ---------------------------------------------------------------- tickers ---

@dataclass
class TickerStat:
    symbol: str          # e.g. SOLUSDT
    base: str            # e.g. SOL
    quote_volume: float  # 24h quoted (USD) volume
    last_price: float
    change_pct_24h: float
    ts: float = field(default_factory=time.time)


_TICKER_MEM: tuple[float, list[TickerStat]] = (0.0, [])


def _binance_tickers() -> list[TickerStat]:
    import requests

    r = requests.get(
        "https://api.binance.com/api/v3/ticker/24hr",
        params={"symbol": ""} if False else None,
        timeout=15,
        headers={"User-Agent": "void-crypto-trader/2.0"},
    )
    r.raise_for_status()
    out: list[TickerStat] = []
    for row in r.json():
        sym = row.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        try:
            qv = float(row.get("quoteVolume") or 0)
            px = float(row.get("lastPrice") or 0)
            chg = float(row.get("priceChangePercent") or 0)
        except Exception:
            continue
        if qv <= 0 or px <= 0:
            continue
        out.append(TickerStat(sym, sym[:-4], qv, px, chg))
    return out


def get_tickers(use_network: bool = True) -> list[TickerStat]:
    """24h stats for every USDT pair. Network -> disk cache -> memory."""
    global _TICKER_MEM
    now = time.time()
    if _TICKER_MEM[1] and (now - _TICKER_MEM[0]) < min(REFRESH_SEC, 300):
        return _TICKER_MEM[1]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    disk = CACHE_DIR / "tickers_usdt.json"
    stats: list[TickerStat] = []
    if use_network:
        try:
            stats = _binance_tickers()
            tmp = disk.with_suffix(".tmp")
            tmp.write_text(json.dumps(
                [{"symbol": s.symbol, "base": s.base, "quote_volume": s.quote_volume,
                  "last_price": s.last_price, "change_pct_24h": s.change_pct_24h}
                 for s in stats]), encoding="utf-8")
            os.replace(tmp, disk)
        except Exception:
            stats = []
    if not stats and disk.exists():
        try:
            rows = json.loads(disk.read_text(encoding="utf-8"))
            stats = [TickerStat(**r) for r in rows]
        except Exception:
            stats = []
    _TICKER_MEM = (now, stats)
    return stats


# ------------------------------------------------------------- handlers ----

class PairHandler:
    name = "base"

    def apply(self, symbols: list[str]) -> list[str]:
        raise NotImplementedError


class StaticPairList(PairHandler):
    """Like freqtrade's StaticPairList - explicit whitelist passthrough."""

    def __init__(self, symbols: list[str]):
        self.symbols = [s.lower() for s in symbols]

    def apply(self, symbols: list[str]) -> list[str]:
        return self.symbols


class VolumePairList(PairHandler):
    """freqtrade VolumePairList: top-N tradable pairs by 24h quote volume."""

    def __init__(self, number_assets: int = 40, min_value: float = 1e6,
                 interval: str = "1d"):
        self.number_assets = number_assets
        self.min_value = min_value
        self.interval = interval

    def apply(self, symbols: list[str]) -> list[str]:
        stats = get_tickers()
        ranked = sorted(stats, key=lambda s: s.quote_volume, reverse=True)
        picked = [s.base.lower() for s in ranked
                  if s.quote_volume >= self.min_value][: self.number_assets]
        if symbols:  # chain mode: intersect with upstream list, keep upstream order
            keep = set(picked)
            return [s for s in symbols if s.lower() in keep] or picked
        return picked


class AgeFilterPairList(PairHandler):
    """Drop pairs listed fewer than `min_age_hours` ago (candle history proxy)."""

    def __init__(self, min_age_hours: float = 72.0, interval: str = "1d",
                 lookback: int = 120):
        self.min_age_hours = min_age_hours
        self.interval = interval
        self.lookback = lookback

    def apply(self, symbols: list[str]) -> list[str]:
        cutoff = time.time() - self.min_age_hours * 3600.0
        out = []
        for s in symbols:
            cs = get_candles(s, self.interval, self.lookback)
            if cs and cs[0].ts <= cutoff:
                out.append(s)
        return out


class VolatilityFilterPairList(PairHandler):
    """freqtrade-style volatility band: keep pairs with atr_pct in [lo, hi]."""

    def __init__(self, atr_lo_pct: float = 0.3, atr_hi_pct: float = 12.0,
                 interval: str = "1h"):
        self.atr_lo = atr_lo_pct
        self.atr_hi = atr_hi_pct
        self.interval = interval

    def apply(self, symbols: list[str]) -> list[str]:
        from indicators import atr_pct as _atr_pct

        out = []
        for s in symbols:
            cs = get_candles(s, self.interval, 60)
            if len(cs) < 20:
                continue
            v = _atr_pct(cs, 14)
            if v is None:
                continue
            if self.atr_lo <= v <= self.atr_hi:
                out.append(s)
        return out


class PerformanceFilterPairList(PairHandler):
    """Keep only pairs whose current confluence score exceeds `min_score`.

    Mirrors freqtrade's PerformanceFilter (rank by realized performance), but
    uses our weighted trend/momentum/structure score instead of closed-trade
    profit, since this bot trades a rolling universe rather than one strategy.
    """

    def __init__(self, min_score: float = 25.0, interval: str = "1h",
                 max_keep: int = 10):
        self.min_score = min_score
        self.interval = interval
        self.max_keep = max_keep

    def apply(self, symbols: list[str]) -> list[str]:
        from indicators import snapshot, confluence_score

        scored: list[tuple[float, str]] = []
        for s in symbols:
            snap = snapshot(s, self.interval, 250)
            if snap is None or not snap.enough_history:
                continue
            score, _b, _r = confluence_score(snap)
            if score >= self.min_score:
                scored.append((score, s))
        scored.sort(reverse=True)
        return [s for _sc, s in scored[: self.max_keep]]


# ------------------------------------------------------------ convenience --

def liquid_top_symbols(number: int = 20, min_quote_volume: float = 5e6) -> list[str]:
    """Quick top-by-volume scan (the single most important freqtrade lesson:
    liquidity first, everything else second)."""
    stats = sorted(get_tickers(), key=lambda s: s.quote_volume, reverse=True)
    return [s.base.lower() for s in stats if s.quote_volume >= min_quote_volume][:number]


def best_pair(symbol: str) -> Optional[str]:
    """Pick the deepest USDT market for a token across Binance tickers.
    Returns e.g. 'SOLUSDT' or None when unlisted (a rug/honeypot tell)."""
    ex = to_exchange_symbol(symbol)
    for t in get_tickers():
        if t.symbol == ex:
            return ex
    return None


def build_chain(handlers: list[PairHandler], seed: Optional[list[str]] = None) -> list[str]:
    """Run handlers sequentially like freqtrade's PairListManager."""
    syms = [s.lower() for s in (seed or [])]
    for h in handlers:
        syms = h.apply(syms)
        if not syms:
            break
    return syms
