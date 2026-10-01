"""
Dollar-cost average: periodically buy a fixed USD amount of each configured symbol
if we have cash and are under max position size.
"""
from __future__ import annotations
import time
from strategies.base import Strategy, Signal
from portfolio import Portfolio
from config import settings

_last_buy: dict[str, float] = {}


class DCAStrategy(Strategy):
    name = "dca"
    interval = 3600  # seconds between DCA buys per symbol (1h)

    def generate(self, portfolio: Portfolio, prices: dict[str, float]) -> list[Signal]:
        signals: list[Signal] = []
        now = time.time()
        trade_usd = settings.trade_usd
        max_pct = settings.max_position_pct / 100.0
        equity = portfolio.total_equity(prices) or portfolio.cash

        for sym in settings.symbol_list:
            price = prices.get(sym)
            if price is None:
                continue
            last = _last_buy.get(sym, 0)
            if now - last < self.interval:
                continue
            current_val = 0.0
            if sym in portfolio.positions:
                current_val = portfolio.positions[sym].market_value(price)
            if current_val >= equity * max_pct:
                continue
            if portfolio.cash < trade_usd:
                continue
            signals.append(
                Signal("buy", sym, usd_amount=trade_usd, reason="DCA scheduled buy")
            )
            _last_buy[sym] = now

        return signals
