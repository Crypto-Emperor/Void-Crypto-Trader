from .base import Strategy, Signal
from .momentum import MomentumStrategy
from .dca import DCAStrategy
from .mean_reversion import MeanReversionStrategy
from .leaderboard_copy import LeaderboardCopyStrategy
from .fomo_copy import FomoCopyStrategy
from .top_accounts_copy import TopAccountsCopyStrategy
from .pilot_signal import PilotSignalStrategy

STRATEGIES = {
    "momentum": MomentumStrategy,
    "dca": DCAStrategy,
    "mean_reversion": MeanReversionStrategy,
    "leaderboard_copy": LeaderboardCopyStrategy,
    "leaderboard": LeaderboardCopyStrategy,
    "fomo_copy": FomoCopyStrategy,
    "copy": FomoCopyStrategy,
    "top_accounts_copy": TopAccountsCopyStrategy,
    "top_copy": TopAccountsCopyStrategy,
    "top": TopAccountsCopyStrategy,
    "pilot_signal": PilotSignalStrategy,
    "pilot": PilotSignalStrategy,
}

def get_strategy(name: str, account_id: str = "1") -> Strategy:
    key = name.lower()
    cls = STRATEGIES.get(key)
    if not cls:
        raise ValueError(f"Unknown strategy: {name}. Choose from {list(STRATEGIES)}")
    if key in ("fomo_copy", "copy", "top_accounts_copy", "top_copy", "top", "pilot_signal", "pilot"):
        return cls(account_id=account_id)
    return cls()
