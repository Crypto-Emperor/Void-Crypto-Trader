"""
Trade from TradingPilot-style bias scores (long/short with confidence filter).
Still gated by honeypot checks on buys.
"""

from __future__ import annotations
import os
from strategies.base import Strategy, Signal
from portfolio import Portfolio
from config import settings
from pilot import scan_signals
from copy_trading import compute_size


class PilotSignalStrategy(Strategy):
    name = "pilot_signal"

    def __init__(self, account_id: str = "1"):
        self.account_id = account_id

    def generate(self, portfolio: Portfolio, prices: dict[str, float]) -> list[Signal]:
        min_conf = float(os.getenv("PILOT_MIN_CONFIDENCE", "0.45"))
        min_bias = float(os.getenv("PILOT_MIN_BIAS", "1.5"))
        max_buys = int(os.getenv("COPY_MAX_BUYS_PER_TICK", "5"))
        max_positions = int(os.getenv("COPY_MAX_POSITIONS", "12"))
        mode = os.getenv("COPY_SIZE_MODE", "smart")
        fixed_usd = float(os.getenv("COPY_FIXED_USD", str(settings.trade_usd)))
        pct_equity = float(os.getenv("COPY_PCT_EQUITY", "5"))
        scale = float(os.getenv("COPY_SCALE", "0.1"))
        max_usd = float(os.getenv("COPY_MAX_USD", "500"))
        min_buy = float(os.getenv("COPY_MIN_BUY_USD", "10"))

        equity = portfolio.total_equity(prices) or portfolio.cash
        cash = portfolio.cash
        open_syms = set(portfolio.positions.keys())

        board = scan_signals()
        signals: list[Signal] = []

        # sells / reduces first
        for s in board:
            if s.side == "short" and s.symbol in open_syms:
                if abs(s.bias_score) >= min_bias and s.confidence >= min_conf:
                    signals.append(
                        Signal(
                            "sell",
                            s.symbol,
                            amount="100%",
                            reason=(
                                f"pilot SHORT {s.symbol} bias={s.bias_score} "
                                f"conf={s.confidence:.0%} SL={s.stop_loss} TP={s.take_profit}"
                            ),
                        )
                    )

        buys = 0
        for s in board:
            if s.side != "long":
                continue
            if abs(s.bias_score) < min_bias or s.confidence < min_conf:
                continue
            if s.symbol in open_syms:
                continue
            if buys >= max_buys or len(open_syms) + buys >= max_positions:
                break
            if cash < min_buy:
                break
            size = compute_size(
                leader_usd=s.leader_buys * 200,
                follower_equity=equity,
                follower_cash=cash,
                mode=mode,
                fixed_usd=fixed_usd,
                pct_equity=pct_equity,
                scale=scale,
                max_usd=max_usd,
                conviction=max(1, s.leader_buys),
                leader_pnl=0,
                leader_rank=max(1, 10 - int(abs(s.bias_score))),
                min_usd=min_buy,
            )
            if size < min_buy:
                continue
            signals.append(
                Signal(
                    "buy",
                    s.symbol,
                    usd_amount=size,
                    reason=(
                        f"pilot LONG {s.symbol} ${size:.0f} bias={s.bias_score} "
                        f"conf={s.confidence:.0%} entry={s.entry} SL={s.stop_loss} TP={s.take_profit}"
                    ),
                )
            )
            cash -= size
            buys += 1

        return signals
