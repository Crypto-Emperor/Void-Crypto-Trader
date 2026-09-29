"""
PnL rate estimates: per second / minute / hour / day.
Based on total PnL since session or portfolio start time.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from config import BASE_DIR

SESSION_FILE = BASE_DIR / "session_meta.json"


def _load_session() -> dict:
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_session(data: dict) -> None:
    try:
        SESSION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def mark_session_start(equity: float, force: bool = False) -> None:
    data = _load_session()
    if force or not data.get("start_ts"):
        data["start_ts"] = time.time()
        data["start_equity"] = float(equity)
        _save_session(data)


def ensure_session(equity: float, starting_balance: float) -> None:
    data = _load_session()
    if not data.get("start_ts"):
        data["start_ts"] = time.time()
        data["start_equity"] = float(starting_balance or equity)
        _save_session(data)


def compute_rates(
    equity: float,
    starting_balance: float,
    total_pnl: Optional[float] = None,
) -> dict:
    """
    Returns dict with pnl_total, elapsed_sec, per_sec, per_min, per_hour, per_day.
    Uses session start when available; falls back to treating PnL as if earned over 1 hour (marked).
    """
    ensure_session(equity, starting_balance)
    data = _load_session()
    start_ts = float(data.get("start_ts") or time.time())
    elapsed = max(1.0, time.time() - start_ts)

    if total_pnl is None:
        total_pnl = float(equity) - float(starting_balance)

    per_sec = total_pnl / elapsed
    return {
        "pnl_total": total_pnl,
        "elapsed_sec": elapsed,
        "per_sec": per_sec,
        "per_min": per_sec * 60.0,
        "per_hour": per_sec * 3600.0,
        "per_day": per_sec * 86400.0,
        "start_ts": start_ts,
    }


def format_rates_line(rates: dict) -> str:
    s = rates["per_sec"]
    m = rates["per_min"]
    h = rates["per_hour"]
    d = rates["per_day"]
    el = rates["elapsed_sec"]
    if el < 60:
        el_s = f"{el:.0f}s"
    elif el < 3600:
        el_s = f"{el/60:.1f}m"
    else:
        el_s = f"{el/3600:.1f}h"
    return (
        f"PnL rate (session {el_s}): "
        f"${s:+.6f}/sec | ${m:+.4f}/min | ${h:+.2f}/hour | ${d:+.2f}/day"
    )
