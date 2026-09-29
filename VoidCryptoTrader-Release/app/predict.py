"""
Next-move predictions for FOMO copy bot.

This does NOT claim to know the future. It scores short-horizon bias using:
  1) UP-trader flow (how many winners just bought/sold a token)
  2) Simple price momentum when we have multi-tick price memory
  3) Your current exposure (already in / not in position)

Output: direction bias, confidence 0-100, suggested action, rough portfolio impact.
"""

from __future__ import annotations
import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional
from collections import defaultdict, deque

from config import BASE_DIR, settings
from leaderboard import traders_who_are_up, fetch_leaderboard
from copy_trading import fetch_user_trades
from prices import get_price, get_prices
from portfolio import Portfolio

PRICE_HIST_FILE = BASE_DIR / "price_history.json"
_PRED_CACHE: dict[str, tuple[float, "TokenPrediction"]] = {}


@dataclass
class TokenPrediction:
    symbol: str
    bias: str  # bullish | bearish | neutral
    confidence: int  # 0-100
    horizon: str  # e.g. "next few hours"
    drivers: list[str] = field(default_factory=list)
    suggested_action: str = "hold"  # buy | sell | hold | reduce
    score: float = 0.0  # internal -100..+100
    leader_buy_count: int = 0
    leader_sell_count: int = 0
    momentum_pct: Optional[float] = None
    price: Optional[float] = None


@dataclass
class PortfolioForecast:
    current_equity: float
    horizon: str
    expected_equity: float
    low_equity: float
    high_equity: float
    expected_pnl: float
    notes: list[str] = field(default_factory=list)
    token_predictions: list[TokenPrediction] = field(default_factory=list)


# ---------- price memory (for momentum) ----------

def _load_price_hist() -> dict:
    if not PRICE_HIST_FILE.exists():
        return {}
    try:
        return json.loads(PRICE_HIST_FILE.read_text())
    except Exception:
        return {}


def _save_price_hist(hist: dict) -> None:
    # keep last ~50 samples per symbol
    trimmed = {}
    for k, series in hist.items():
        trimmed[k] = series[-50:]
    try:
        PRICE_HIST_FILE.write_text(json.dumps(trimmed))
    except OSError:
        pass


def record_prices(symbols: list[str], source: str = "auto") -> dict[str, float]:
    prices = get_prices(symbols, source)
    hist = _load_price_hist()
    now = time.time()
    for sym, px in prices.items():
        series = hist.get(sym, [])
        series.append({"t": now, "p": px})
        hist[sym] = series
    _save_price_hist(hist)
    return prices


def momentum_pct(symbol: str, lookback_sec: int = 900) -> Optional[float]:
    """% change over roughly lookback_sec using stored samples.

    Ignores absurd jumps (e.g. demo price mixed with live) so pilot board
    stays usable.
    """
    hist = _load_price_hist().get(symbol.lower(), [])
    if len(hist) < 2:
        return None
    now = time.time()
    latest = hist[-1]["p"]
    if not latest or latest <= 0:
        return None
    older = None
    for pt in reversed(hist[:-1]):
        if now - pt["t"] >= lookback_sec * 0.4:
            older = pt["p"]
            break
    if older is None:
        # need at least ~2 minutes of history for a meaningful print
        if now - hist[0]["t"] < 120:
            return None
        older = hist[0]["p"]
    if not older or older <= 0:
        return None
    pct = ((latest - older) / older) * 100.0
    # discard data-quality spikes (demo->live or bad tick)
    if abs(pct) > 25:
        return None
    return pct


# ---------- core scoring ----------

def score_token(
    symbol: str,
    leader_buys: int,
    leader_sells: int,
    leader_buy_usd: float,
    in_position: bool,
    price: Optional[float],
) -> TokenPrediction:
    score = 0.0
    drivers: list[str] = []

    # Leader flow (primary signal for this bot)
    if leader_buys > 0:
        score += min(leader_buys * 18, 45)
        drivers.append(f"{leader_buys} UP trader buy(s)")
    if leader_sells > 0:
        score -= min(leader_sells * 20, 50)
        drivers.append(f"{leader_sells} UP trader sell(s)")
    if leader_buy_usd >= 1000:
        score += 8
        drivers.append(f"leader size ~${leader_buy_usd:,.0f}")
    elif leader_buy_usd >= 300:
        score += 4

    mom = momentum_pct(symbol)
    if mom is not None:
        if mom > 3:
            score += min(mom, 15)
            drivers.append(f"momentum +{mom:.1f}%")
        elif mom < -3:
            score += max(mom, -15)  # mom negative
            drivers.append(f"momentum {mom:.1f}%")

    if in_position and score < -10:
        drivers.append("you hold this - risk of follow-through sell")
    if not in_position and score > 20:
        drivers.append("not in position - possible entry window")

    # clamp
    score = max(-100.0, min(100.0, score))

    if score >= 25:
        bias = "bullish"
        action = "buy" if not in_position else "hold"
    elif score <= -25:
        bias = "bearish"
        action = "sell" if in_position else "hold"
    else:
        bias = "neutral"
        action = "hold"
        if in_position and score < -10:
            action = "reduce"

    confidence = int(min(95, abs(score) * 0.9 + (10 if leader_buys + leader_sells >= 2 else 0)))

    return TokenPrediction(
        symbol=symbol,
        bias=bias,
        confidence=confidence,
        horizon="next few hours (activity-based)",
        drivers=drivers or ["insufficient signal"],
        suggested_action=action,
        score=score,
        leader_buy_count=leader_buys,
        leader_sell_count=leader_sells,
        momentum_pct=mom,
        price=price,
    )


def gather_leader_flow(
    window: str = "24h",
    top_n: int = 10,
    min_pnl: float = 0.0,
) -> dict[str, dict]:
    """
    Aggregate recent buy/sell counts from UP traders per symbol.
    Returns symbol -> {buys, sells, buy_usd, handles}
    """
    winners = traders_who_are_up(window=window, limit=top_n, min_pnl=min_pnl)
    flow: dict[str, dict] = defaultdict(lambda: {"buys": 0, "sells": 0, "buy_usd": 0.0, "handles": []})
    for trader in winners:
        for tr in fetch_user_trades(trader.handle, limit=12):
            sym = (tr.symbol or "").lower()
            if not sym or sym in ("usdc", "usdt", "usd", "unknown"):
                continue
            if tr.status == "open" or tr.side_hint == "buy":
                flow[sym]["buys"] += 1
                flow[sym]["buy_usd"] += max(tr.usd_value, 0)
                flow[sym]["handles"].append(trader.handle)
            elif tr.status == "closed" or tr.side_hint == "sell":
                flow[sym]["sells"] += 1
                flow[sym]["handles"].append(trader.handle)
    return flow


def predict_tokens(
    symbols: list[str] | None = None,
    portfolio: Portfolio | None = None,
) -> list[TokenPrediction]:
    pf = portfolio or Portfolio.load()
    window = getattr(settings, "leaderboard_window", "24h")
    top_n = int(getattr(settings, "leaderboard_top_n", 10) or 10)

    flow = gather_leader_flow(window=window, top_n=top_n)
    watch = set(symbols or [])
    watch |= set(flow.keys())
    watch |= set(pf.positions.keys())
    watch |= set(settings.symbol_list)
    watch = {s.lower() for s in watch if s}

    prices = record_prices(list(watch), settings.price_source)
    preds: list[TokenPrediction] = []
    for sym in sorted(watch):
        f = flow.get(sym, {"buys": 0, "sells": 0, "buy_usd": 0.0})
        pred = score_token(
            sym,
            leader_buys=f["buys"],
            leader_sells=f["sells"],
            leader_buy_usd=f["buy_usd"],
            in_position=sym in pf.positions,
            price=prices.get(sym),
        )
        preds.append(pred)

    preds.sort(key=lambda p: abs(p.score), reverse=True)
    return preds


def forecast_portfolio(portfolio: Portfolio | None = None) -> PortfolioForecast:
    """
    Rough next-session equity band from token biases.
    Bullish holdings / planned buys nudge expected equity up; bearish the opposite.
    """
    pf = portfolio or Portfolio.load()
    preds = predict_tokens(portfolio=pf)
    prices = get_prices(list(pf.positions.keys()) + settings.symbol_list, settings.price_source)
    current = pf.total_equity(prices)

    # map bias to expected short-horizon return contribution
    expected = current
    low = current
    high = current
    notes: list[str] = []

    for p in preds:
        if p.symbol not in pf.positions and p.suggested_action != "buy":
            continue
        weight = 0.0
        if p.symbol in pf.positions and prices.get(p.symbol):
            weight = pf.positions[p.symbol].market_value(prices[p.symbol]) / max(current, 1)
        elif p.suggested_action == "buy":
            weight = min(0.05, settings.trade_usd / max(current, 1))

        # confidence-scaled move assumption: ±1% to ±4% over horizon
        move = (p.score / 100.0) * (0.02 + 0.02 * (p.confidence / 100.0))
        delta = current * weight * move
        expected += delta
        low += current * weight * (move - 0.03)
        high += current * weight * (move + 0.03)

        if abs(p.score) >= 25:
            notes.append(
                f"{p.symbol.upper()}: {p.bias} conf={p.confidence}% -> {p.suggested_action}"
            )

    if not notes:
        notes.append("No strong directional signals - expect choppy / sideways action")

    notes.append(
        "Prediction is heuristic (leader flow + momentum), not financial advice."
    )

    return PortfolioForecast(
        current_equity=round(current, 2),
        horizon="next few hours",
        expected_equity=round(expected, 2),
        low_equity=round(max(low, 0), 2),
        high_equity=round(high, 2),
        expected_pnl=round(expected - current, 2),
        notes=notes[:12],
        token_predictions=preds[:15],
    )
