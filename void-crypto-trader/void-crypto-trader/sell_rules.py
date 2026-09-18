"""
Defensive sells: exit when price is dropping or predicted to drop.

Used by auto_trader every tick (all strategies).
"""

from __future__ import annotations
import os
from strategies.base import Signal
from portfolio import Portfolio


def defensive_sell_signals(
    portfolio: Portfolio,
    prices: dict[str, float],
) -> list[Signal]:
    """
    For each open position, emit sell if:
      - short momentum is clearly negative, or
      - predictor says bearish / sell / reduce, or
      - free market 1h/24h change is strongly negative
    """
    if not portfolio.positions:
        return []

    drop_mom = float(os.getenv("SELL_MOMENTUM_PCT", "-3"))  # e.g. -3% lookback
    drop_1h = float(os.getenv("SELL_CHANGE_1H", "-2"))
    drop_24h = float(os.getenv("SELL_CHANGE_24H", "-8"))
    min_conf = int(os.getenv("SELL_PRED_CONFIDENCE", "40"))
    partial = os.getenv("SELL_PARTIAL_ON_REDUCE", "true").lower() in ("1", "true", "yes")

    signals: list[Signal] = []
    open_syms = list(portfolio.positions.keys())

    # --- predictions ---
    pred_map = {}
    try:
        from predict import predict_tokens, record_prices

        record_prices(open_syms, "auto")
        for p in predict_tokens(open_syms, portfolio=portfolio):
            pred_map[p.symbol.lower()] = p
    except Exception:
        pass

    # --- free market stats ---
    free_map = {}
    try:
        from free_api import free_hot_tokens

        for t in free_hot_tokens(80):
            free_map[(t.get("symbol") or "").lower()] = t
    except Exception:
        pass

    # --- momentum ---
    try:
        from predict import momentum_pct
    except Exception:
        momentum_pct = lambda *_a, **_k: None  # type: ignore

    for sym in open_syms:
        reasons = []
        action_amt = None  # "100%" or "50%"

        mom = momentum_pct(sym)
        if mom is not None and mom <= drop_mom:
            reasons.append(f"momentum {mom:.1f}% <= {drop_mom}%")
            action_amt = "100%"

        ft = free_map.get(sym)
        if ft:
            ch1 = float(ft.get("change_1h") or 0)
            ch24 = float(ft.get("change_h24") or 0)
            if ch1 <= drop_1h:
                reasons.append(f"1h change {ch1:.1f}%")
                action_amt = "100%"
            elif ch24 <= drop_24h:
                reasons.append(f"24h change {ch24:.1f}%")
                action_amt = action_amt or "100%"

        pred = pred_map.get(sym)
        if pred:
            if pred.suggested_action == "sell" and pred.confidence >= min_conf:
                reasons.append(f"predict SELL conf={pred.confidence}% ({pred.bias})")
                action_amt = "100%"
            elif pred.suggested_action == "reduce" and pred.confidence >= min_conf:
                reasons.append(f"predict REDUCE conf={pred.confidence}%")
                action_amt = "50%" if partial else "100%"
            elif pred.bias == "bearish" and pred.confidence >= min_conf + 10:
                reasons.append(f"predict bearish conf={pred.confidence}%")
                action_amt = action_amt or ("50%" if partial else "100%")

        # price vs entry if we have avg entry on position
        try:
            pos = portfolio.positions[sym]
            px = prices.get(sym)
            entry = getattr(pos, "avg_price", None) or getattr(pos, "entry_price", None)
            if px and entry and entry > 0:
                pnl_pct = (px - entry) / entry * 100.0
                # stop-loss style: down 8% from entry
                stop = float(os.getenv("SELL_STOP_LOSS_PCT", "-8"))
                if pnl_pct <= stop:
                    reasons.append(f"stop-loss {pnl_pct:.1f}% from entry")
                    action_amt = "100%"
        except Exception:
            pass

        if reasons and action_amt:
            signals.append(
                Signal(
                    "sell",
                    sym,
                    amount=action_amt,
                    reason="defensive: " + "; ".join(reasons[:3]),
                )
            )

    return signals
