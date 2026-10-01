"""
Technical indicators - pure python, no numpy / TA-Lib dependency.

Implements the toolset that professional crypto traders actually publish and
teach, so the bot can act on it:

  Trend        SMA, EMA, MACD, ADX/DMI, Choppiness Index, Donchian channels
  Momentum     RSI (Wilder), Stochastic %K/%D, ROC, Connors RSI-2 pullback rule
  Volatility   Wilder ATR, Bollinger Bands + %B + Bandwidth, realized vol
  Volume       OBV, volume ratio vs baseline, VWAP (session)
  Structure    swing highs/lows, market structure (HH/HL vs LH/LL)
  Composite    -100..+100 "confluence" score used by strategies

Sources of these definitions: Wilder (New Concepts in Technical Trading
Systems), Appel (MACD), Bollinger, Elder's Triple Screen, Turtle Trading
(Donchian breakout + ATR sizing, N = ATR), Chande & Kroll (Stochastic RSI),
Connors (RSI(2)<10 mean-reversion), Kirkpatrick & Dahlquist (Choppiness).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from market_data import Candle, get_candles


# ---------- primitives ----------

def sma(values: Sequence[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    window = sum(values[:period])
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def ema(values: Sequence[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    n = len(values)
    if n == 0 or period <= 0 or n < period:
        return out
    k = 2.0 / (period + 1.0)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def last(series: list[Optional[float]]) -> Optional[float]:
    for v in reversed(series):
        if v is not None:
            return v
    return None


def _wilder_smooth(values: Sequence[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    n = len(values)
    if n < period or period <= 0:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


# ---------- volatility ----------

def true_range(candles: Sequence[Candle]) -> list[float]:
    trs: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c.range)
            continue
        p = candles[i - 1].close
        trs.append(max(c.high - c.low, abs(c.high - p), abs(c.low - p)))
    return trs


def atr(candles: Sequence[Candle], period: int = 14) -> list[Optional[float]]:
    return _wilder_smooth(true_range(candles), period)


def atr_pct(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    """ATR as % of price - comparable across BTC and memecoins."""
    a = last(atr(candles, period))
    px = candles[-1].close if candles else 0.0
    if not a or px <= 0:
        return None
    return a / px * 100.0


def realized_vol_pct(closes: Sequence[float], lookback: int = 48) -> Optional[float]:
    """Std-dev of log returns over `lookback` bars (% per bar)."""
    if len(closes) < 3:
        return None
    rets = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a > 0 and b > 0:
            rets.append(math.log(b / a))
    use = rets[-lookback:]
    if len(use) < 3:
        return None
    m = sum(use) / len(use)
    var = sum((r - m) ** 2 for r in use) / (len(use) - 1)
    return math.sqrt(var) * 100.0


def bollinger(closes: Sequence[float], period: int = 20, mult: float = 2.0) -> dict:
    n = len(closes)
    mid_u = sma(closes, period)
    lo_u: list[Optional[float]] = [None] * n
    up_u: list[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        m = sum(window) / period
        var = sum((x - m) ** 2 for x in window) / period
        sd = math.sqrt(var)
        up_u[i] = m + mult * sd
        lo_u[i] = m - mult * sd
    pctb: Optional[float] = None
    bandwidth: Optional[float] = None
    if mid_u[-1] and up_u[-1] is not None and lo_u[-1] is not None and n:
        rng = up_u[-1] - lo_u[-1]
        if rng > 0:
            pctb = (closes[-1] - lo_u[-1]) / rng
        bandwidth = rng / mid_u[-1] * 100.0
    return {"mid": last(mid_u), "upper": last(up_u), "lower": last(lo_u),
            "pct_b": pctb, "bandwidth_pct": bandwidth}


# ---------- trend ----------

def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    ef, es = ema(closes, fast), ema(closes, slow)
    line: list[Optional[float]] = [
        (a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)
    ]
    valid = [v for v in line if v is not None]
    sig_full = ema(valid, signal)
    sig: list[Optional[float]] = [None] * len(line)
    offset = len(line) - len(valid)
    for i, v in enumerate(sig_full):
        if v is not None:
            sig[offset + i] = v
    hist: list[Optional[float]] = [
        (a - b) if (a is not None and b is not None) else None for a, b in zip(line, sig)
    ]
    return {"macd": last(line), "signal": last(sig), "hist": last(hist),
            "hist_series": hist, "line_series": line, "signal_series": sig}


def adx(candles: Sequence[Candle], period: int = 14) -> dict:
    """Average Directional Index + DI+/DI- (Wilder)."""
    n = len(candles)
    if n < period + 2:
        return {"adx": None, "plus_di": None, "minus_di": None}
    trs, pdms, ndms = [], [], []
    for i in range(1, n):
        c, p = candles[i], candles[i - 1]
        up = c.high - p.high
        dn = p.low - c.low
        pdms.append(up if (up > dn and up > 0) else 0.0)
        ndms.append(dn if (dn > up and dn > 0) else 0.0)
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    atr_s = _wilder_smooth(trs, period)
    ps = _wilder_smooth(pdms, period)
    ns = _wilder_smooth(ndms, period)
    dxs: list[float] = []
    for i in range(len(trs)):
        a, p_, n_ = atr_s[i], ps[i], ns[i]
        if a is None or p_ is None or n_ is None or a <= 0:
            continue
        pdi = 100.0 * p_ / a
        ndi = 100.0 * n_ / a
        denom = pdi + ndi
        dxs.append(100.0 * abs(pdi - ndi) / denom if denom > 0 else 0.0)
    adx_s = _wilder_smooth(dxs, period)
    last_atr = last(atr_s) or 0.0
    pdi = 100.0 * (last(ps) or 0.0) / last_atr if last_atr else None
    ndi = 100.0 * (last(ns) or 0.0) / last_atr if last_atr else None
    return {"adx": last(adx_s), "plus_di": pdi, "minus_di": ndi}


def choppiness(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    """Chande & Kroll. >61.8 = chop/range, <38.2 = trending."""
    n = len(candles)
    if n < period + 1:
        return None
    trs = true_range(candles)[1:]
    sum_tr = sum(trs[-period:])
    hi = max(c.high for c in candles[-period:])
    lo = min(c.low for c in candles[-period:])
    rng = hi - lo
    if sum_tr <= 0 or rng <= 0:
        return None
    return 100.0 * math.log10(sum_tr / rng) / math.log10(period)


def donchian(candles: Sequence[Candle], period: int = 20) -> Optional[dict]:
    n = len(candles)
    if n < period + 1:
        return None
    window = candles[-(period + 1):-1]
    upper = max(c.high for c in window)
    lower = min(c.low for c in window)
    px = candles[-1].close
    return {"upper": upper, "lower": lower, "mid": (upper + lower) / 2.0,
            "break_up": px > upper, "break_down": px < lower}


# ---------- momentum ----------

def rsi(closes: Sequence[float], period: int = 14) -> list[Optional[float]]:
    n = len(closes)
    out: list[Optional[float]] = [None] * n
    if n < period + 1:
        return out
    gains, losses = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = _wilder_smooth(gains, period)
    al = _wilder_smooth(losses, period)
    for i in range(len(ag)):
        g, l = ag[i], al[i]
        if g is None or l is None:
            continue
        if l == 0:
            out[i + 1] = 100.0
        else:
            rs = g / l
            out[i + 1] = 100.0 - 100.0 / (1.0 + rs)
    return out


def rsi_last(closes: Sequence[float], period: int = 14) -> Optional[float]:
    return last(rsi(closes, period))


def stochastic(candles: Sequence[Candle], k_period: int = 14, d_period: int = 3) -> dict:
    n = len(candles)
    if n < k_period + 1:
        return {"k": None, "d": None}
    ks: list[float] = []
    for i in range(n):
        w = candles[max(0, i - k_period + 1):i + 1]
        lo_i, hi_i = min(c.low for c in w), max(c.high for c in w)
        rng = hi_i - lo_i
        ks.append(50.0 if rng <= 0 else (candles[i].close - lo_i) / rng * 100.0)
    d_window = ks[-d_period:]
    return {"k": ks[-1], "d": sum(d_window) / len(d_window) if d_window else None}


def roc(closes: Sequence[float], bars: int = 12) -> Optional[float]:
    if len(closes) <= bars:
        return None
    a, b = closes[-1 - bars], closes[-1]
    if a <= 0:
        return None
    return (b - a) / a * 100.0


# ---------- volume ----------

def obv(candles: Sequence[Candle]) -> list[float]:
    out = [0.0]
    for i in range(1, len(candles)):
        d = 0.0
        if candles[i].close > candles[i - 1].close:
            d = candles[i].volume
        elif candles[i].close < candles[i - 1].close:
            d = -candles[i].volume
        out.append(out[-1] + d)
    return out


def volume_ratio(candles: Sequence[Candle], period: int = 20) -> Optional[float]:
    """Latest bar volume vs average of previous `period` bars (>1 = heavy tape)."""
    if len(candles) < period + 1:
        return None
    base = sum(c.volume for c in candles[-(period + 1):-1]) / period
    if base <= 0:
        return None
    return candles[-1].volume / base


def volume_trend(candles: Sequence[Candle], period: int = 20) -> Optional[float]:
    if len(candles) < period * 2:
        return None
    recent = sum(c.volume for c in candles[-period:]) / period
    prior = sum(c.volume for c in candles[-2 * period:-period]) / period
    if prior <= 0:
        return None
    return (recent - prior) / prior * 100.0


def vwap(candles: Sequence[Candle], period: int = 24) -> Optional[float]:
    if len(candles) < 2:
        return None
    num = den = 0.0
    for c in candles[-period:]:
        tp = (c.high + c.low + c.close) / 3.0
        num += tp * c.volume
        den += c.volume
    return num / den if den > 0 else None


# ---------- structure ----------

def swings(candles: Sequence[Candle], look: int = 3):
    """Fractal swing highs/lows -> (highs, lows) as (index, price)."""
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(look, len(candles) - look):
        w = candles[i - look:i + look + 1]
        if candles[i].high >= max(c.high for c in w):
            highs.append((i, candles[i].high))
        if candles[i].low <= min(c.low for c in w):
            lows.append((i, candles[i].low))
    return highs, lows


def market_structure(candles: Sequence[Candle], look: int = 3) -> str:
    """'uptrend' (HH+HL), 'downtrend' (LH+LL), 'mixed', 'unknown'."""
    highs, lows = swings(candles, look)
    if len(highs) < 2 or len(lows) < 2:
        return "unknown"
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    if hh and hl:
        return "uptrend"
    if lh and ll:
        return "downtrend"
    return "mixed"


# ---------- composite ----------

@dataclass
class Snapshot:
    symbol: str
    interval: str
    price: float
    atr_pct: Optional[float] = None
    realized_vol_pct: Optional[float] = None
    rsi_fast: Optional[float] = None      # RSI(2) - Connors pullback rule
    rsi: Optional[float] = None           # RSI(14)
    macd_hist: Optional[float] = None
    adx: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None
    chop: Optional[float] = None
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    ema_long: Optional[float] = None
    close_vs_ema_slow: Optional[float] = None
    pct_b: Optional[float] = None
    bb_bandwidth: Optional[float] = None
    donchian_break_up: bool = False
    donchian_break_down: bool = False
    structure: str = "unknown"
    volume_ratio: Optional[float] = None
    volume_trend_pct: Optional[float] = None
    vwap: Optional[float] = None
    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None
    roc_1h: Optional[float] = None
    roc_4h: Optional[float] = None
    roc_24h: Optional[float] = None
    bars: int = 0

    @property
    def enough_history(self) -> bool:
        return self.bars >= 30


def snapshot(symbol: str, interval: str = "1h", limit: int = 250) -> Optional[Snapshot]:
    candles = get_candles(symbol, interval, limit)
    if len(candles) < 5:
        return None
    closes = [c.close for c in candles]
    px = closes[-1]

    e_fast, e_slow, e_long = ema(closes, 9), ema(closes, 21), ema(closes, 55)
    bb = bollinger(closes, 20, 2.0)
    dc = donchian(candles, 20)
    ad = adx(candles, 14)
    st = stochastic(candles, 14, 3)
    es = last(e_slow)

    snap = Snapshot(
        symbol=symbol.lower(),
        interval=interval,
        price=px,
        atr_pct=atr_pct(candles, 14),
        realized_vol_pct=realized_vol_pct(closes, min(48, len(closes) - 1)),
        rsi_fast=rsi_last(closes, 2),
        rsi=rsi_last(closes, 14),
        macd_hist=macd(closes)["hist"],
        adx=ad["adx"], plus_di=ad["plus_di"], minus_di=ad["minus_di"],
        chop=choppiness(candles, 14),
        ema_fast=last(e_fast), ema_slow=es, ema_long=last(e_long),
        close_vs_ema_slow=((px - es) / es * 100.0) if es else None,
        pct_b=bb["pct_b"], bb_bandwidth=bb["bandwidth_pct"],
        donchian_break_up=bool(dc and dc["break_up"]),
        donchian_break_down=bool(dc and dc["break_down"]),
        structure=market_structure(candles, 3),
        volume_ratio=volume_ratio(candles, 20),
        volume_trend_pct=volume_trend(candles, 20),
        vwap=vwap(candles, 24),
        stoch_k=st["k"], stoch_d=st["d"],
        bars=len(candles),
    )
    try:
        from market_data import change_pct as _cp
        snap.roc_1h = _cp(symbol, "1h", 1)
        snap.roc_4h = _cp(symbol, "1h", 4)
        snap.roc_24h = _cp(symbol, "1h", 24)
    except Exception:
        pass
    return snap


def confluence_score(snap: Snapshot) -> tuple[float, list[str], list[str]]:
    """
    Weighted trend / momentum / structure / volume confluence in [-100, +100].
    Positive = long bias, negative = short-or-exit bias.
    Returns (score, bull_reasons, bear_reasons).
    """
    bulls: list[str] = []
    bears: list[str] = []
    score = 0.0

    def add(weight: float, cond: bool, txt: str, into: list[str]):
        nonlocal score
        if cond:
            score += weight
            into.append(txt)

    if snap.ema_fast and snap.ema_slow:
        add(14, snap.ema_fast > snap.ema_slow, "EMA9>EMA21 uptrend", bulls)
        add(14, snap.ema_fast < snap.ema_slow, "EMA9<EMA21 downtrend", bears)
    if snap.ema_slow and snap.price:
        add(8, snap.price > snap.ema_slow,
            f"price above EMA21 ({snap.close_vs_ema_slow:+.1f}%)", bulls)
        add(8, snap.price < snap.ema_slow,
            f"price below EMA21 ({(snap.close_vs_ema_slow or 0):+.1f}%)", bears)
    if snap.ema_long and snap.price:
        add(8, snap.price > snap.ema_long, "above EMA55 (macro trend)", bulls)
        add(8, snap.price < snap.ema_long, "below EMA55 (macro downtrend)", bears)
    if snap.macd_hist is not None and snap.price:
        rel = snap.macd_hist / max(snap.price, 1e-9) * 100.0
        add(10, rel > 0.02, f"MACD hist +{rel:.2f}%", bulls)
        add(10, rel < -0.02, f"MACD hist {rel:.2f}%", bears)
    add(10, snap.structure == "uptrend", "structure: higher highs/lows", bulls)
    add(10, snap.structure == "downtrend", "structure: lower highs/lows", bears)
    add(12, snap.donchian_break_up, "Donchian 20 breakout UP", bulls)
    add(12, snap.donchian_break_down, "Donchian 20 breakdown", bears)

    if snap.adx is not None and snap.plus_di is not None and snap.minus_di is not None:
        if snap.adx >= 25:
            add(12, snap.plus_di > snap.minus_di,
                f"ADX {snap.adx:.0f} bullish (DI+ {snap.plus_di:.0f})", bulls)
            add(12, snap.minus_di >= snap.plus_di,
                f"ADX {snap.adx:.0f} bearish (DI- {snap.minus_di:.0f})", bears)
        else:
            bulls.append(f"ADX {snap.adx:.0f} weak trend (chop)")

    if snap.rsi is not None:
        add(6, 45 <= snap.rsi <= 70, f"RSI14 {snap.rsi:.0f} healthy momentum", bulls)
        add(8, snap.rsi > 78, f"RSI14 {snap.rsi:.0f} overbought", bears)
        add(6, snap.rsi < 38, f"RSI14 {snap.rsi:.0f} weak", bears)

    if snap.pct_b is not None:
        add(8, snap.pct_b > 1.02, f"Bollinger %{snap.pct_b:.2f} stretched", bears)
        add(6, snap.pct_b < -0.02, f"Bollinger %{snap.pct_b:.2f} capitulation zone", bears)

    if snap.volume_ratio is not None:
        add(8, snap.volume_ratio >= 1.5, f"volume {snap.volume_ratio:.1f}x baseline", bulls)
        add(5, snap.volume_ratio <= 0.6, f"volume drying up ({snap.volume_ratio:.1f}x)", bears)

    if snap.chop is not None and snap.chop > 61.8:
        bears.append(f"choppy tape (CHOP {snap.chop:.0f})")

    score = max(-100.0, min(100.0, score))
    return round(score, 1), bulls, bears


def regime(snap: Snapshot) -> str:
    """Regime label used to decide which strategy should lead."""
    if snap.chop is not None and snap.chop > 61.8:
        return "chop"
    if snap.adx is not None and snap.adx >= 25:
        return "trending"
    return "neutral"
