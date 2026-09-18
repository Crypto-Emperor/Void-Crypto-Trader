from __future__ import annotations
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import Optional
from portfolio import Portfolio


@dataclass
class Signal:
    action: str  # buy | sell | hold
    symbol: str
    usd_amount: Optional[float] = None   # for buy
    amount: Optional[str | float] = None  # for sell ("50%", "all", or tokens)
    reason: str = ""


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, portfolio: Portfolio, prices: dict[str, float]) -> list[Signal]:
        """Return 0+ signals to execute this tick."""
        ...
