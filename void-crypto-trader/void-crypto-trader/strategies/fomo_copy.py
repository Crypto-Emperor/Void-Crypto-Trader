"""
True FOMO copy-trading strategy: mirror followed leaders' opens/closes.
"""

from __future__ import annotations
from strategies.base import Strategy, Signal
from portfolio import Portfolio
from config import settings
from copy_trading import collect_copy_signals, mark_seen


class FomoCopyStrategy(Strategy):
    name = "fomo_copy"

    def __init__(self, account_id: str = "1"):
        self.account_id = account_id

    def generate(self, portfolio: Portfolio, prices: dict[str, float]) -> list[Signal]:
        equity = portfolio.total_equity(prices) or portfolio.cash
        open_syms = set(portfolio.positions.keys())
        raw = collect_copy_signals(
            account_id=self.account_id,
            follower_equity=equity,
            follower_cash=portfolio.cash,
            open_position_symbols=open_syms,
        )
        signals: list[Signal] = []
        for c in raw:
            if c.action == "buy" and c.usd_amount:
                # Prefer mint for honeypot scan when available
                symbol = c.mint or c.symbol
                signals.append(
                    Signal(
                        "buy",
                        symbol if c.mint and len(c.symbol) > 12 else c.symbol,
                        usd_amount=c.usd_amount,
                        reason=c.reason,
                    )
                )
                # mark after we attempt (auto_trader will execute; mark here to avoid re-fire)
                mark_seen(c.trade_id)
            elif c.action == "sell":
                signals.append(
                    Signal("sell", c.symbol, amount=c.amount or "100%", reason=c.reason)
                )
                mark_seen(c.trade_id)
        return signals
