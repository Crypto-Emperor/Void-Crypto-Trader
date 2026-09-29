"""
Multi-account support (up to 5).
Each account has its own portfolio state file, capital, and optional FOMO creds.
"""

from __future__ import annotations
import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional
from config import BASE_DIR, settings

ACCOUNTS_FILE = BASE_DIR / "accounts.json"
MAX_ACCOUNTS = 5


@dataclass
class Account:
    id: str
    name: str
    starting_balance: float = 10000.0
    mode: str = "sim"  # sim | live
    enabled: bool = True
    # optional live FOMO credentials per account
    fomo_email: Optional[str] = None
    fomo_password: Optional[str] = None
    fomo_access_token: Optional[str] = None
    live_max_usd: float = 200.0
    # strategy override (None = use global STRATEGY)
    strategy: Optional[str] = None

    @property
    def state_file(self) -> Path:
        return BASE_DIR / f"portfolio_{self.id}.json"

    @property
    def is_live(self) -> bool:
        return self.mode == "live" and bool(
            self.fomo_access_token or self.fomo_email
        )


def _default_accounts() -> list[Account]:
    return [
        Account(
            id="1",
            name="main",
            starting_balance=settings.starting_balance,
            mode=settings.mode,
        )
    ]


def load_accounts() -> list[Account]:
    if not ACCOUNTS_FILE.exists():
        accs = _default_accounts()
        save_accounts(accs)
        return accs
    try:
        raw = json.loads(ACCOUNTS_FILE.read_text())
        accs = []
        for row in raw.get("accounts", [])[:MAX_ACCOUNTS]:
            accs.append(Account(**{k: v for k, v in row.items() if k in Account.__dataclass_fields__}))
        if not accs:
            accs = _default_accounts()
            save_accounts(accs)
        return accs
    except Exception:
        return _default_accounts()


def save_accounts(accounts: list[Account]) -> None:
    data = {"accounts": [asdict(a) for a in accounts[:MAX_ACCOUNTS]]}
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2))


def get_account(account_id: str) -> Optional[Account]:
    for a in load_accounts():
        if a.id == account_id or a.name == account_id:
            return a
    return None


def enabled_accounts() -> list[Account]:
    return [a for a in load_accounts() if a.enabled]


def add_account(
    name: str,
    starting_balance: float = 10000.0,
    mode: str = "sim",
) -> Account:
    accs = load_accounts()
    if len(accs) >= MAX_ACCOUNTS:
        raise ValueError(f"Max {MAX_ACCOUNTS} accounts")
    used = {a.id for a in accs}
    new_id = next(str(i) for i in range(1, MAX_ACCOUNTS + 1) if str(i) not in used)
    acc = Account(id=new_id, name=name, starting_balance=starting_balance, mode=mode)
    accs.append(acc)
    save_accounts(accs)
    return acc
