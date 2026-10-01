"""
Copy-trade style strategy driven by FOMO leaderboard consensus.

- Fetch top traders for a window (24h / 7d)
- Tokens that appear in multiple top traders' holdings → BUY
- Tokens we hold that dropped off consensus → partial SELL
"""

from __future__ import annotations
from strategies.base import Strategy, Signal
from portfolio import Portfolio
from config import settings
from leaderboard import top_token_consensus, fetch_leaderboard


class LeaderboardCopyStrategy(Strategy):
    name = "leaderboard_copy"

    def generate(self, portfolio: Portfolio, prices: dict[str, float]) -> list[Signal]:
        signals: list[Signal] = []
        window = getattr(settings, "leaderboard_window", None) or "24h"
        top_n = int(getattr(settings, "leaderboard_top_n", 10) or 10)
        min_mentions = int(getattr(settings, "leaderboard_min_mentions", 2) or 2)

        consensus = top_token_consensus(window=window, top_n_traders=top_n, min_mentions=min_mentions)
        trade_usd = settings.trade_usd
        max_pct = settings.max_position_pct / 100.0
        equity = portfolio.total_equity(prices) or portfolio.cash

        # BUY consensus tokens we can price
        for token, mentions in sorted(consensus.items(), key=lambda x: -x[1]):
            # prefer symbols we already price; skip raw unknown mints unless in prices
            sym = token.lower()
            price = prices.get(sym)
            if price is None:
                # try only known symbol-like keys (not long mints) for buy via our price feed
                if len(sym) > 15:
                    continue
                from prices import get_price
                price = get_price(sym, settings.price_source)
                if price is None:
                    continue
                prices[sym] = price

            current_val = 0.0
            if sym in portfolio.positions:
                current_val = portfolio.positions[sym].market_value(price)
            if current_val >= equity * max_pct:
                continue
            if portfolio.cash < trade_usd * 0.5:
                break
            size = min(trade_usd, portfolio.cash * 0.75)
            if size < 10:
                continue
            signals.append(
                Signal(
                    "buy",
                    sym,
                    usd_amount=size,
                    reason=f"leaderboard consensus {mentions}/{top_n} top traders ({window})",
                )
            )

        # SELL positions not in consensus (weak hold)
        consensus_keys = set(consensus.keys())
        for sym, pos in list(portfolio.positions.items()):
            if sym in ("usdc", "usdt"):
                continue
            if sym not in consensus_keys and sym not in {t.lower() for t in settings.symbol_list if t}:
                # only exit if it was likely a leaderboard-driven name (optional soft rule)
                # sell 25% if not on board at all
                signals.append(
                    Signal(
                        "sell",
                        sym,
                        amount="25%",
                        reason=f"not in top-{top_n} leaderboard consensus ({window})",
                    )
                )

        return signals[:5]  # cap signals per tick
