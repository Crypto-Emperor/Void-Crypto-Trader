"""
TradingPilot-style signal board for the FOMO bot.

- Ranked signals with bias + confidence
- Entry / stop-loss / take-profit suggestions
- Signal flips vs previous scan
- Biggest movers on the watchlist

Heuristic only — not financial advice.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from config import BASE_DIR, settings
from prices import get_price

STATE_FILE = BASE_DIR / "pilot_state.json"


@dataclass
class PilotSignal:
    symbol: str
    side: str  # long | short | neutral
    bias_score: float  # -6 .. +6
    confidence: float  # 0..1
    price: Optional[float]
    entry: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    risk_reward: Optional[float]
    drivers: list[str] = field(default_factory=list)
    leader_buys: int = 0
    leader_sells: int = 0
    momentum_pct: Optional[float] = None
    flipped: bool = False
    prev_side: Optional[str] = None


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"signals": {}, "ts": 0}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"signals": {}, "ts": 0}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def _bias_from_score(raw: float) -> tuple[str, float]:
    b = max(-6.0, min(6.0, raw / 15.0))
    if b >= 1.5:
        side = "long"
    elif b <= -1.5:
        side = "short"
    else:
        side = "neutral"
    return side, round(b, 2)


def _levels(
    price: float, side: str, atr_pct: float = 0.03
) -> tuple[float, float, float, Optional[float]]:
    entry = price
    if side == "long":
        sl = price * (1 - atr_pct)
        tp = price * (1 + atr_pct * 2.0)
    elif side == "short":
        sl = price * (1 + atr_pct)
        tp = price * (1 - atr_pct * 2.0)
    else:
        sl = price * (1 - atr_pct)
        tp = price * (1 + atr_pct)
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = round(reward / risk, 2) if risk > 0 else None
    return entry, sl, tp, rr


def _safe_momentum(symbol: str, lookback_sec: int = 1200) -> Optional[float]:
    try:
        from predict import momentum_pct

        return momentum_pct(symbol, lookback_sec=lookback_sec)
    except Exception:
        return None


def _safe_record_prices(symbols: list[str], source: str = "auto") -> dict[str, float]:
    try:
        from predict import record_prices

        return record_prices(symbols, source) or {}
    except Exception:
        out: dict[str, float] = {}
        for s in symbols:
            try:
                px = get_price(s, source)
                if px:
                    out[s] = float(px)
            except Exception:
                pass
        return out


def _safe_leader_flow(window: str = "24h", top_n: int = 10) -> dict:
    try:
        from predict import gather_leader_flow

        return gather_leader_flow(window=window, top_n=top_n) or {}
    except Exception:
        # free-market proxy flow from free_api
        flow: dict = {}
        try:
            from free_api import free_hot_tokens

            for t in free_hot_tokens(30):
                sym = (t.get("symbol") or "").lower()
                if not sym:
                    continue
                ch = float(t.get("change_h24") or 0)
                vol = float(t.get("volume_h24") or 0)
                buys = 1 if ch > 2 else 0
                sells = 1 if ch < -2 else 0
                flow[sym] = {
                    "buys": buys,
                    "sells": sells,
                    "buy_usd": vol * 0.01 if buys else 0.0,
                    "handles": [f"mkt_{sym}"],
                }
        except Exception:
            pass
        return flow


def build_signal(
    symbol: str,
    leader_buys: int,
    leader_sells: int,
    leader_buy_usd: float,
    price: Optional[float],
    prev_side: Optional[str],
) -> PilotSignal:
    raw = 0.0
    drivers: list[str] = []

    if leader_buys:
        raw += min(leader_buys * 12, 36)
        drivers.append(f"{leader_buys} UP buys")
    if leader_sells:
        raw -= min(leader_sells * 14, 40)
        drivers.append(f"{leader_sells} UP sells")
    if leader_buy_usd >= 1000:
        raw += 8
        drivers.append(f"size ~${leader_buy_usd:,.0f}")
    elif leader_buy_usd >= 300:
        raw += 4

    mom = _safe_momentum(symbol)
    if mom is not None:
        if mom > 2:
            raw += min(mom * 1.2, 18)
            drivers.append(f"mom +{mom:.1f}%")
        elif mom < -2:
            raw += max(mom * 1.2, -18)
            drivers.append(f"mom {mom:.1f}%")
        if abs(mom) > 12:
            raw *= 0.85
            drivers.append("extended move — size caution")

    side, bias_score = _bias_from_score(raw)
    conf = min(
        0.95,
        abs(bias_score) / 6.0 * 0.75
        + (0.15 if leader_buys + leader_sells >= 2 else 0.05),
    )
    conf = round(conf, 2)

    entry = sl = tp = rr = None
    if price and price > 0 and side != "neutral":
        atr = 0.04 if (mom is not None and abs(mom) > 5) else 0.03
        if symbol in ("btc", "eth", "sol"):
            atr = 0.02
        entry, sl, tp, rr = _levels(price, side, atr_pct=atr)

    flipped = bool(prev_side and prev_side != side and side != "neutral")
    if not drivers:
        drivers.append("weak / mixed tape")

    return PilotSignal(
        symbol=symbol,
        side=side,
        bias_score=bias_score,
        confidence=conf,
        price=price,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=rr,
        drivers=drivers,
        leader_buys=leader_buys,
        leader_sells=leader_sells,
        momentum_pct=mom,
        flipped=flipped,
        prev_side=prev_side,
    )


def scan_signals(
    symbols: list[str] | None = None,
    top_n: int | None = None,
) -> list[PilotSignal]:
    window = getattr(settings, "leaderboard_window", "24h") or "24h"
    top_n = top_n or int(getattr(settings, "leaderboard_top_n", 10) or 10)
    flow = _safe_leader_flow(window=window, top_n=top_n)

    watch = set(symbols or [])
    watch |= set(flow.keys())
    try:
        watch |= set(settings.symbol_list)
    except Exception:
        watch |= {"sol", "eth", "btc"}
    # free hot symbols
    try:
        from free_api import free_hot_tokens

        for t in free_hot_tokens(15):
            if t.get("symbol"):
                watch.add(str(t["symbol"]).lower())
    except Exception:
        pass

    watch = {s.lower() for s in watch if s}
    prices = _safe_record_prices(list(watch), getattr(settings, "price_source", "auto"))
    state = _load_state()
    prev_map = state.get("signals") or {}

    signals: list[PilotSignal] = []
    for sym in watch:
        f = flow.get(sym) or {"buys": 0, "sells": 0, "buy_usd": 0.0}
        prev = prev_map.get(sym) or {}
        prev_side = prev.get("side")
        px = prices.get(sym)
        if px is None:
            try:
                px = get_price(sym, getattr(settings, "price_source", "auto"))
            except Exception:
                px = None
        sig = build_signal(
            sym,
            leader_buys=int(f.get("buys") or 0),
            leader_sells=int(f.get("sells") or 0),
            leader_buy_usd=float(f.get("buy_usd") or 0),
            price=float(px) if px else None,
            prev_side=prev_side,
        )
        signals.append(sig)

    def rank_key(s: PilotSignal):
        action = 0 if s.side == "neutral" else 1
        return (action, abs(s.bias_score), s.confidence)

    signals.sort(key=rank_key, reverse=True)

    state["signals"] = {
        s.symbol: {
            "side": s.side,
            "bias_score": s.bias_score,
            "confidence": s.confidence,
        }
        for s in signals
    }
    state["ts"] = time.time()
    _save_state(state)
    return signals


def biggest_movers(
    symbols: list[str] | None = None, limit: int = 8
) -> list[tuple[str, float, Optional[float]]]:
    try:
        watch = list({*(symbols or []), *settings.symbol_list})
    except Exception:
        watch = list(symbols or ["sol", "eth", "btc"])
    _safe_record_prices(watch, getattr(settings, "price_source", "auto"))
    rows: list[tuple[str, float, Optional[float]]] = []
    for sym in watch:
        m = _safe_momentum(sym)
        if m is None:
            continue
        try:
            px = get_price(sym, getattr(settings, "price_source", "auto"))
        except Exception:
            px = None
        rows.append((sym, m, px))
    rows.sort(key=lambda x: abs(x[1]), reverse=True)
    return rows[:limit]


def pilot_dashboard(limit: int = 12) -> dict:
    signals = scan_signals()
    movers = biggest_movers(limit=8)
    flips = [s for s in signals if s.flipped]
    longs = [s for s in signals if s.side == "long"]
    shorts = [s for s in signals if s.side == "short"]
    return {
        "signals": signals[:limit],
        "flips": flips[:8],
        "longs": longs[:8],
        "shorts": shorts[:8],
        "movers": movers,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }
