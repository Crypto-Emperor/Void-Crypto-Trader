"""
Real-time FOMO scan: find who is UP, copy their trades, size each buy intelligently.
"""

from __future__ import annotations
import os
from strategies.base import Strategy, Signal
from portfolio import Portfolio
from config import settings
from leaderboard import traders_who_are_up
from copy_trading import (
    fetch_user_trades,
    already_seen,
    mark_seen,
    compute_size,
)


class TopAccountsCopyStrategy(Strategy):
    name = "top_accounts_copy"

    def __init__(self, account_id: str = "1"):
        self.account_id = account_id

    def generate(self, portfolio: Portfolio, prices: dict[str, float]) -> list[Signal]:
        window = getattr(settings, "leaderboard_window", None) or "24h"
        top_n = int(getattr(settings, "leaderboard_top_n", 10) or 10)
        min_pnl = float(os.getenv("COPY_MIN_PNL_USD", "0"))
        max_buys_per_tick = int(os.getenv("COPY_MAX_BUYS_PER_TICK", "5"))
        max_positions = int(os.getenv("COPY_MAX_POSITIONS", "12"))
        min_leader_usd = float(os.getenv("COPY_MIN_LEADER_USD", "50"))
        min_buy_usd = float(os.getenv("COPY_MIN_BUY_USD", "10"))

        mode = os.getenv("COPY_SIZE_MODE", "smart")
        fixed_usd = float(os.getenv("COPY_FIXED_USD", str(settings.trade_usd)))
        pct_equity = float(os.getenv("COPY_PCT_EQUITY", "5"))
        scale = float(os.getenv("COPY_SCALE", "0.1"))
        max_usd = float(
            os.getenv(
                "COPY_MAX_USD",
                str(settings.live_max_usd if settings.is_live else max(settings.trade_usd * 5, 500)),
            )
        )

        equity = portfolio.total_equity(prices) or portfolio.cash
        cash = portfolio.cash
        open_syms = set(portfolio.positions.keys())

        winners = traders_who_are_up(window=window, limit=top_n, min_pnl=min_pnl)
        if not winners:
            return []

        # symbol -> aggregation
        buy_weights: dict[str, float] = {}
        buy_reasons: dict[str, list[str]] = {}
        buy_trade_ids: dict[str, list[str]] = {}
        best_rank: dict[str, int] = {}
        best_pnl: dict[str, float] = {}
        sell_signals: list[Signal] = []

        for trader in winners:
            trades = fetch_user_trades(trader.handle, limit=20)
            for tr in trades:
                if already_seen(tr.trade_id):
                    continue
                sym = (tr.symbol or "").lower()
                if not sym or sym in ("usdc", "usdt", "usd", "unknown"):
                    mark_seen(tr.trade_id)
                    continue
                if tr.usd_value and tr.usd_value < min_leader_usd:
                    mark_seen(tr.trade_id)
                    continue

                is_buy = tr.status == "open" or tr.side_hint == "buy"
                is_sell = tr.status == "closed" or tr.side_hint == "sell"

                if is_buy:
                    buy_weights[sym] = buy_weights.get(sym, 0.0) + max(tr.usd_value, 0)
                    buy_reasons.setdefault(sym, []).append(
                        f"@{trader.handle}(+${trader.pnl_usd:,.0f})"
                    )
                    buy_trade_ids.setdefault(sym, []).append(tr.trade_id)
                    best_rank[sym] = min(best_rank.get(sym, 999), trader.rank)
                    best_pnl[sym] = max(best_pnl.get(sym, 0.0), trader.pnl_usd)
                elif is_sell:
                    if sym in open_syms:
                        sell_signals.append(
                            Signal(
                                "sell",
                                sym,
                                amount="100%",
                                reason=(
                                    f"UP trader @{trader.handle} "
                                    f"(+${trader.pnl_usd:,.0f}) SELL {sym}"
                                ),
                            )
                        )
                    mark_seen(tr.trade_id)

        buy_signals: list[Signal] = []
        ranked = sorted(buy_weights.items(), key=lambda x: -x[1])
        positions_now = len(open_syms)

        for sym, leader_usd in ranked:
            if len(buy_signals) >= max_buys_per_tick:
                break
            if positions_now + len(buy_signals) >= max_positions:
                break
            if cash < min_buy_usd:
                break

            conviction = len(set(buy_reasons.get(sym, [])))
            size = compute_size(
                leader_usd,
                equity,
                cash,
                mode,
                fixed_usd,
                pct_equity,
                scale,
                max_usd,
                conviction=conviction,
                leader_pnl=best_pnl.get(sym, 0.0),
                leader_rank=best_rank.get(sym, 10),
                min_usd=min_buy_usd,
            )
            if size < min_buy_usd:
                for tid in buy_trade_ids.get(sym, []):
                    mark_seen(tid)
                continue

            leaders = ",".join(buy_reasons.get(sym, [])[:4])
            buy_signals.append(
                Signal(
                    "buy",
                    sym,
                    usd_amount=size,
                    reason=(
                        f"copy UP BUY {sym} ${size:.0f} "
                        f"(conv={conviction}, rank={best_rank.get(sym)}, {leaders})"
                    ),
                )
            )
            cash -= size
            for tid in buy_trade_ids.get(sym, []):
                mark_seen(tid)

        return sell_signals[:max_buys_per_tick] + buy_signals
