"""
Simple momentum: if price is up vs a short moving average of recent observed prices,
buy; if down, sell part of position.
Uses an in-memory price history (resets on restart – fine for practice).
"""
from __future__ import annotations
from collections import defaultdict, deque
from strategies.base import Strategy, Signal
from portfolio import Portfolio
from config import settings

_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))


class MomentumStrategy(Strategy):
    name = "momentum"

    def generate(self, portfolio: Portfolio, prices: dict[str, float]) -> list[Signal]:
        signals: list[Signal] = []
        trade_usd = settings.trade_usd
        max_pct = settings.max_position_pct / 100.0
        equity = portfolio.total_equity(prices) or portfolio.cash

        for sym, price in prices.items():
            hist = _history[sym]
            hist.append(price)
            if len(hist) < 5:
                continue

            ma = sum(hist) / len(hist)
            change = (price - ma) / ma if ma else 0

            # Strong up → buy
            if change > 0.015:  # >1.5% above MA
                # don't over-allocate
                current_val = 0.0
                if sym in portfolio.positions:
                    current_val = portfolio.positions[sym].market_value(price)
                if current_val < equity * max_pct and portfolio.cash >= trade_usd:
                    signals.append(
                        Signal("buy", sym, usd_amount=min(trade_usd, portfolio.cash * 0.75),
                               reason=f"momentum +{change*100:.1f}% vs MA")
                    )

            # Strong down → sell 30%
            elif change < -0.02 and sym in portfolio.positions:
                signals.append(
                    Signal("sell", sym, amount="30%", reason=f"momentum {change*100:.1f}% vs MA")
                )

        return signals
