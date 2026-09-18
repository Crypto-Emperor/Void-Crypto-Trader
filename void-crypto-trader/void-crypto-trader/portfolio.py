"""
Portfolio + trade execution interface.
Sim executor is fully implemented.
Live FOMO executor is in executor/live_fomo.py and is called when mode=live.
"""

from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from config import STATE_FILE, settings
from prices import get_price

FEE_RATE = 0.005  # ~FOMO memecoin fee


@dataclass
class Position:
    symbol: str
    amount: float
    avg_entry: float
    opened_at: str

    def market_value(self, price: float) -> float:
        return self.amount * price

    def pnl(self, price: float) -> float:
        return (price - self.avg_entry) * self.amount

    def pnl_pct(self, price: float) -> float:
        if self.avg_entry == 0:
            return 0.0
        return ((price - self.avg_entry) / self.avg_entry) * 100


@dataclass
class Trade:
    side: str
    symbol: str
    amount: float
    price: float
    usd_value: float
    fee: float
    timestamp: str
    mode: str
    note: str = ""


@dataclass
class Portfolio:
    cash: float = settings.starting_balance
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    starting_balance: float = settings.starting_balance
    mode: str = "sim"

    def total_equity(self, prices: dict[str, float]) -> float:
        eq = self.cash
        for sym, pos in self.positions.items():
            p = prices.get(sym.lower())
            if p is not None:
                eq += pos.market_value(p)
        return eq

    def save(self, path: Path = STATE_FILE) -> None:
        data = {
            "cash": self.cash,
            "starting_balance": self.starting_balance,
            "mode": self.mode,
            "positions": {k: asdict(v) for k, v in self.positions.items()},
            "trades": [asdict(t) for t in self.trades[-300:]],
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "Portfolio":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            positions = {k: Position(**v) for k, v in data.get("positions", {}).items()}
            trades = [Trade(**t) for t in data.get("trades", [])]
            return cls(
                cash=float(data.get("cash", settings.starting_balance)),
                positions=positions,
                trades=trades,
                starting_balance=float(data.get("starting_balance", settings.starting_balance)),
                mode=data.get("mode", "sim"),
            )
        except Exception:
            return cls()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_buy(
    portfolio: Portfolio,
    symbol: str,
    usd_amount: float,
    price: Optional[float] = None,
    source: str = "auto",
    note: str = "",
    skip_honeypot: bool = False,
) -> tuple[bool, str]:
    symbol = symbol.lower()
    if usd_amount <= 0:
        return False, "Amount must be > 0"

    # Hard cap: max MAX_POSITION_PCT of equity (default 6%)
    try:
        prices_est = {}
        eq = portfolio.total_equity(prices_est) if hasattr(portfolio, "total_equity") else portfolio.cash
        if not eq:
            eq = portfolio.cash
        pct = float(getattr(settings, "max_position_pct", 6) or 6) / 100.0
        cap = eq * pct
        if usd_amount > cap + 1e-6:
            usd_amount = cap
            if usd_amount <= 0:
                return False, "6% equity cap is zero"
    except Exception:
        pass

    # Cap live risk
    if portfolio.mode == "live" and usd_amount > settings.live_max_usd:
        return False, f"Live trade exceeds LIVE_MAX_USD (${settings.live_max_usd})"

    # Strict honeypot / sellability check (GoPlus)
    if settings.honeypot_check and not skip_honeypot:
        from security import check_before_buy
        allowed, sec_msg, _ = check_before_buy(
            symbol,
            chain=settings.default_chain,
            strict=settings.honeypot_strict,
        )
        if not allowed:
            return False, f"BLOCKED by honeypot check: {sec_msg}"
        if sec_msg and "allow-listed" not in sec_msg:
            note = (note + " | " if note else "") + sec_msg

    if price is None:
        price = get_price(symbol, source)
    if price is None or price <= 0:
        return False, f"No price for {symbol}"

    # Live path
    if portfolio.mode == "live" and settings.is_live:
        try:
            from executor.live_fomo import live_buy
            ok, msg = live_buy(symbol, usd_amount)
            if not ok:
                return False, f"LIVE buy failed: {msg}"
        except Exception as e:
            return False, f"LIVE executor error: {e}"

    fee = usd_amount * FEE_RATE
    net = usd_amount - fee
    if net > portfolio.cash + 1e-9:
        return False, f"Insufficient cash (${portfolio.cash:.2f})"

    tokens = net / price
    if symbol in portfolio.positions:
        pos = portfolio.positions[symbol]
        total_cost = pos.avg_entry * pos.amount + net
        pos.amount += tokens
        pos.avg_entry = total_cost / pos.amount
    else:
        portfolio.positions[symbol] = Position(symbol, tokens, price, _now())

    portfolio.cash -= usd_amount
    portfolio.trades.append(
        Trade("buy", symbol, tokens, price, usd_amount, fee, _now(), portfolio.mode, note)
    )
    portfolio.save()
    return True, f"BUY {tokens:.6f} {symbol.upper()} @ ${price:.6f} (fee ${fee:.2f}) [{portfolio.mode}]"


def execute_sell(
    portfolio: Portfolio,
    symbol: str,
    amount: float | str,
    price: Optional[float] = None,
    source: str = "auto",
    note: str = "",
) -> tuple[bool, str]:
    symbol = symbol.lower()
    if symbol not in portfolio.positions:
        return False, f"No position in {symbol}"

    pos = portfolio.positions[symbol]
    if isinstance(amount, str):
        a = amount.strip().lower()
        if a in ("all", "100%"):
            tokens = pos.amount
        elif a.endswith("%"):
            tokens = pos.amount * (float(a[:-1]) / 100)
        else:
            tokens = float(a)
    else:
        tokens = float(amount)

    if tokens <= 0 or tokens > pos.amount + 1e-9:
        return False, f"Invalid amount (have {pos.amount:.6f})"

    if price is None:
        price = get_price(symbol, source)
    if price is None or price <= 0:
        return False, f"No price for {symbol}"

    if portfolio.mode == "live" and settings.is_live:
        try:
            from executor.live_fomo import live_sell
            ok, msg = live_sell(symbol, tokens)
            if not ok:
                return False, f"LIVE sell failed: {msg}"
        except Exception as e:
            return False, f"LIVE executor error: {e}"

    usd_gross = tokens * price
    fee = usd_gross * FEE_RATE
    usd_net = usd_gross - fee

    pos.amount -= tokens
    if pos.amount < 1e-10:
        del portfolio.positions[symbol]

    portfolio.cash += usd_net
    portfolio.trades.append(
        Trade("sell", symbol, tokens, price, usd_gross, fee, _now(), portfolio.mode, note)
    )
    portfolio.save()
    return True, f"SELL {tokens:.6f} {symbol.upper()} @ ${price:.6f} -> +${usd_net:.2f} [{portfolio.mode}]"


def load_for_account(account) -> "Portfolio":
    """Load portfolio bound to an Account object from accounts.py."""
    from config import settings as _s
    pf = Portfolio.load(account.state_file)
    if pf.starting_balance == _s.starting_balance and pf.cash == _s.starting_balance and not pf.trades:
        # fresh file - seed from account
        pf.starting_balance = account.starting_balance
        pf.cash = account.starting_balance
    pf.mode = "live" if account.is_live else "sim"
    return pf


def save_for_account(portfolio: "Portfolio", account) -> None:
    portfolio.save(account.state_file)

