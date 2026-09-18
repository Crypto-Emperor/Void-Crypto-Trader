"""
Mean reversion: buy when price is significantly below short MA, sell when above.
"""
from __future__ import annotations
from collections import defaultdict, deque
from strategies.base import Strategy, Signal
from portfolio import Portfolio
from config import settings

_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=30))


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def generate(self, portfolio: Portfolio, prices: dict[str, float]) -> list[Signal]:
        signals: list[Signal] = []
        trade_usd = settings.trade_usd
        equity = portfolio.total_equity(prices) or portfolio.cash
        max_pct = settings.max_position_pct / 100.0

        for sym, price in prices.items():
            hist = _history[sym]
            hist.append(price)
            if len(hist) < 8:
                continue
            ma = sum(hist) / len(hist)
            dev = (price - ma) / ma if ma else 0

            if dev < -0.03 and portfolio.cash >= trade_usd:
                current_val = 0.0
                if sym in portfolio.positions:
                    current_val = portfolio.positions[sym].market_value(price)
                if current_val < equity * max_pct:
                    signals.append(
                        Signal("buy", sym, usd_amount=trade_usd,
                               reason=f"mean-rev {dev*100:.1f}% below MA")
                    )
            elif dev > 0.04 and sym in portfolio.positions:
                signals.append(
                    Signal("sell", sym, amount="40%", reason=f"mean-rev {dev*100:.1f}% above MA")
                )

        return signals
