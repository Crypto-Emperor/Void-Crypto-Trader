"""
Quarterly (and longer) profit projections based on:
1) Historical realized performance of this portfolio (if enough trades)
2) Configurable expected annual return + volatility bands
"""

from __future__ import annotations
import math
from datetime import datetime, timezone
from portfolio import Portfolio
from prices import get_prices
from config import settings


def _realized_annualized(pf: Portfolio, prices: dict[str, float]) -> float | None:
    """Rough annualized return from portfolio start → now."""
    equity = pf.total_equity(prices)
    if pf.starting_balance <= 0 or not pf.trades:
        return None
    # use first trade timestamp as start
    try:
        t0 = datetime.fromisoformat(pf.trades[0].timestamp.replace("Z", "+00:00"))
        days = max((datetime.now(timezone.utc) - t0).total_seconds() / 86400, 1)
        total_return = (equity / pf.starting_balance) - 1
        annualized = (1 + total_return) ** (365 / days) - 1
        return annualized
    except Exception:
        return None


def project_quarter(
    pf: Portfolio | None = None,
    expected_annual: float | None = None,
) -> dict:
    """
    Project next quarter (≈90 days) equity.
    Returns base / optimistic / pessimistic USD values and notes.
    """
    if pf is None:
        pf = Portfolio.load()
    prices = get_prices(list(pf.positions.keys()) + settings.symbol_list, settings.price_source)
    current = pf.total_equity(prices)

    # Prefer realized if we have history, else config expectation
    realized = _realized_annualized(pf, prices)
    mu = expected_annual if expected_annual is not None else (
        realized if realized is not None else settings.expected_annual_return
    )
    # clamp extreme realized numbers for projection sanity
    mu = max(min(mu, 2.0), -0.8)

    sigma = settings.volatility
    # quarterly ≈ annual / 4 for drift; vol scales with sqrt(time)
    q_mu = mu / 4
    q_sigma = sigma * math.sqrt(0.25)

    base = current * (1 + q_mu)
    opt = current * (1 + q_mu + q_sigma)   # ~ +1σ
    pess = current * (1 + q_mu - q_sigma)  # ~ -1σ

    return {
        "current_equity": round(current, 2),
        "starting_balance": pf.starting_balance,
        "expected_annual_return_used": round(mu, 4),
        "realized_annualized": round(realized, 4) if realized is not None else None,
        "quarter_base": round(base, 2),
        "quarter_optimistic": round(opt, 2),
        "quarter_pessimistic": round(max(pess, 0), 2),
        "quarter_base_pnl": round(base - current, 2),
        "note": (
            "Projection uses realized performance when available, otherwise "
            "EXPECTED_ANNUAL_RETURN from .env. Crypto is highly uncertain."
        ),
    }
