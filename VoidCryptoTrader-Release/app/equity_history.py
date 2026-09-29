"""
Persist equity samples for portfolio charts.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from config import BASE_DIR

HISTORY_FILE = BASE_DIR / "equity_history.json"
MAX_POINTS = 2000


def _load() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save(rows: list[dict]) -> None:
    try:
        HISTORY_FILE.write_text(json.dumps(rows[-MAX_POINTS:], indent=2), encoding="utf-8")
    except Exception:
        pass


def record_equity(equity: float, cash: float = 0.0, pnl: float = 0.0) -> None:
    rows = _load()
    rows.append({
        "ts": time.time(),
        "equity": float(equity),
        "cash": float(cash),
        "pnl": float(pnl),
    })
    _save(rows)


def get_series() -> tuple[list[float], list[float]]:
    rows = _load()
    xs = [r.get("ts", 0) for r in rows]
    ys = [r.get("equity", 0) for r in rows]
    return xs, ys


def clear_history() -> None:
    _save([])
