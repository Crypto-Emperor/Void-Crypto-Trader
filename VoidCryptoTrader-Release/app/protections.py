"""
Trading protections - a port of freqtrade's protection plugins
(https://github.com/freqtrade/freqtrade/tree/develop/freqtrade/plugins/protections).

freqtrade runs four built-in protectors *before every entry*:

  CooldownPeriod    - after ANY exit, lock the pair for N candles (stop chop)
  StoplossGuard     - N stoploss exits within a lookback -> global trading halt
  LowProfitPairs    - pair-level: recent closed trades unprofitable -> lock pair
  MaxDrawdown       - account-level: drawdown over recent trades -> global halt

Our `risk.py` already has equity-based circuit breakers; this module adds the
missing *per-pair* locks and trade-log-driven guards, using the same
lookback / stop-duration semantics as freqtrade (minutes, candle-aware).

State (locks) persists to JSON so restarts don't forget why a pair is frozen.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from config import BASE_DIR

STATE_FILE = Path(os.getenv("PROTECTIONS_STATE", str(BASE_DIR / "protections_state.json")))


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


@dataclass
class ProtectionReturn:
    """Mirror of freqtrade's ProtectionReturn dataclass."""
    lock: bool
    until: float           # unix ts when the lock expires
    reason: str
    scope: str = "pair"    # "pair" | "global"
    symbol: str = ""


@dataclass
class TradeRecord:
    """Minimal closed-trade record the guards evaluate (like LocalTrade)."""
    symbol: str
    close_ts: float
    profit_pct: float      # realized % on the position
    exit_tag: str = ""     # "stop" | "hard_stop" | "trail_stop" | "chandelier" ...


STOP_TAGS = {"stop", "hard_stop", "trail_stop", "chandelier", "stoploss"}


class Protections:
    """Runs all enabled protections and maintains pair/global locks."""

    def __init__(self) -> None:
        tf_min = _f("TF_MINUTES", 60.0)  # our base timeframe is 1h candles

        # --- cooldown period (freqtrade CooldownPeriod) ---
        self.cooldown_enabled = os.getenv("COOLDOWN_ENABLED", "true").lower() in ("1", "true", "yes")
        self.cooldown_candles = int(_f("COOLDOWN_CANDLES", 3))
        self.cooldown_sec = self.cooldown_candles * tf_min * 60.0

        # --- stoploss guard (freqtrade StoplossGuard) ---
        self.sl_guard_enabled = os.getenv("STOPLOSS_GUARD", "true").lower() in ("1", "true", "yes")
        self.sl_trade_limit = int(_f("STOPLOSS_GUARD_TRADES", 4))
        self.sl_lookback_sec = int(_f("STOPLOSS_GUARD_LOOKBACK_MIN", 720)) * 60.0
        self.sl_stop_sec = int(_f("STOPLOSS_GUARD_STOP_MIN", 240)) * 60.0
        self.sl_profit_limit = _f("STOPLOSS_GUARD_PROFIT_LIMIT", 0.0)  # pct

        # --- low profit pairs (freqtrade LowProfitPairs) ---
        self.low_profit_enabled = os.getenv("LOW_PROFIT_LOCK", "true").lower() in ("1", "true", "yes")
        self.lp_trade_limit = int(_f("LOW_PROFIT_TRADES", 3))
        self.lp_lookback_sec = int(_f("LOW_PROFIT_LOOKBACK_MIN", 2880)) * 60.0
        self.lp_stop_sec = int(_f("LOW_PROFIT_STOP_MIN", 720)) * 60.0
        self.lp_required_profit = _f("LOW_PROFIT_REQUIRED_PCT", 1.0)

        # --- max drawdown over recent trades (freqtrade MaxDrawdown) ---
        self.mdd_enabled = os.getenv("TRADE_MAX_DRAWDOWN", "true").lower() in ("1", "true", "yes")
        self.mdd_trade_limit = int(_f("TRADE_MDD_TRADES", 10))
        self.mdd_lookback_sec = int(_f("TRADE_MDD_LOOKBACK_MIN", 2880)) * 60.0
        self.mdd_stop_sec = int(_f("TRADE_MDD_STOP_MIN", 360)) * 60.0
        self.mdd_max_allowed = _f("TRADE_MDD_MAX_PCT", 8.0)  # % compounded loss allowed

        self._pair_locks: dict[str, float] = {}      # symbol -> unlock ts
        self._lock_reasons: dict[str, str] = {}
        self._global_lock_until: float = 0.0
        self._global_lock_reason: str = ""
        self._load()

    # ------------------------------------------------------------ state ----

    def _load(self) -> None:
        try:
            if STATE_FILE.exists():
                d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self._pair_locks = {k: float(v) for k, v in d.get("pair_locks", {}).items()}
                self._lock_reasons = dict(d.get("lock_reasons", {}))
                self._global_lock_until = float(d.get("global_until", 0.0))
                self._global_lock_reason = d.get("global_reason", "")
        except Exception:
            pass

    def _save(self) -> None:
        try:
            STATE_FILE.write_text(json.dumps({
                "pair_locks": self._pair_locks,
                "lock_reasons": self._lock_reasons,
                "global_until": self._global_lock_until,
                "global_reason": self._global_lock_reason,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }), encoding="utf-8")
        except Exception:
            pass

    # ----------------------------------------------------------- guards ----

    def _cooldown(self, symbol: str, trades: Sequence[TradeRecord], now: float) -> Optional[ProtectionReturn]:
        """Pair locked for N candles after its most recent exit."""
        if not self.cooldown_enabled:
            return None
        last_exit = max((t.close_ts for t in trades if t.symbol == symbol), default=0.0)
        if last_exit <= 0:
            return None
        until = last_exit + self.cooldown_sec
        if now < until:
            return ProtectionReturn(True, until,
                                    f"cooldown: exited {int(now - last_exit)}s ago, "
                                    f"{self.cooldown_candles} candle(s)",
                                    scope="pair", symbol=symbol)
        return None

    def _stoploss_guard(self, trades: Sequence[TradeRecord], now: float) -> Optional[ProtectionReturn]:
        """Global halt if >= limit stop-exits with profit < threshold in lookback."""
        if not self.sl_guard_enabled:
            return None
        window = [t for t in trades
                  if t.exit_tag in STOP_TAGS
                  and t.profit_pct < self.sl_profit_limit
                  and t.close_ts >= now - self.sl_lookback_sec]
        if len(window) < self.sl_trade_limit:
            return None
        until = now + self.sl_stop_sec
        return ProtectionReturn(True, until,
                                f"{len(window)} stoploss exits (<{self.sl_profit_limit:.1f}%) "
                                f"within {int(self.sl_lookback_sec // 60)} min - trading halted",
                                scope="global")

    def _low_profit_pairs(self, symbol: str, trades: Sequence[TradeRecord],
                          now: float) -> Optional[ProtectionReturn]:
        """Per-pair lock when the last few closed trades on it lost money."""
        if not self.low_profit_enabled:
            return None
        window = [t for t in trades if t.symbol == symbol and t.close_ts >= now - self.lp_lookback_sec]
        if len(window) < self.lp_trade_limit:
            return None
        total = sum(t.profit_pct for t in window[-self.lp_trade_limit:])
        if total < self.lp_required_profit:
            until = now + self.lp_stop_sec
            return ProtectionReturn(True, until,
                                    f"last {self.lp_trade_limit} closed trades on {symbol.upper()} "
                                    f"summed {total:+.1f}% (< {self.lp_required_profit:.1f}%) - pair locked",
                                    scope="pair", symbol=symbol)
        return None

    def _max_drawdown(self, trades: Sequence[TradeRecord], now: float) -> Optional[ProtectionReturn]:
        """Global halt when compounded recent-trade drawdown exceeds allowance."""
        if not self.mdd_enabled:
            return None
        window = sorted((t for t in trades if t.close_ts >= now - self.mdd_lookback_sec),
                        key=lambda t: t.close_ts)
        if len(window) < self.mdd_trade_limit:
            return None
        recent = window[-self.mdd_trade_limit:]
        equity_mult = 1.0
        peak = 1.0
        dd = 0.0
        for t in recent:
            equity_mult *= (1.0 + t.profit_pct / 100.0)
            peak = max(peak, equity_mult)
            dd = max(dd, (peak - equity_mult) / peak * 100.0)
        if dd > self.mdd_max_allowed:
            until = now + self.mdd_stop_sec
            return ProtectionReturn(True, until,
                                    f"trade drawdown {dd:.1f}% > {self.mdd_max_allowed:.1f}% "
                                    f"over last {len(recent)} trades - trading halted",
                                    scope="global")
        return None

    # ------------------------------------------------------------ public ---

    def _register(self, r: ProtectionReturn) -> None:
        if r.scope == "global":
            self._global_lock_until = max(self._global_lock_until, r.until)
            self._global_lock_reason = r.reason
        else:
            sym = r.symbol.lower()
            self._pair_locks[sym] = max(self._pair_locks.get(sym, 0.0), r.until)
            self._lock_reasons[sym] = r.reason

    def run_all(self, symbol: str, trades: Sequence[TradeRecord],
                now: Optional[float] = None) -> list[ProtectionReturn]:
        """Evaluate every protection for one candidate entry. Never raises."""
        now = now or time.time()
        results: list[ProtectionReturn] = []
        guards = (
            lambda: self._cooldown(symbol, trades, now),
            lambda: self._stoploss_guard(trades, now),
            lambda: self._low_profit_pairs(symbol, trades, now),
            lambda: self._max_drawdown(trades, now),
        )
        for g in guards:
            try:
                r = g()
            except Exception:
                r = None
            if r and r.lock:
                results.append(r)
                self._register(r)
        if results:
            self._save()
        return results

    def note_exit(self, symbol: str, ts: Optional[float] = None) -> None:
        """Explicit cooldown stamp (freqtrade CooldownPeriod applies right after
        an exit closes, not only once the trade log is re-parsed)."""
        if not self.cooldown_enabled:
            return
        ts = ts or time.time()
        until = ts + self.cooldown_sec
        sym = symbol.lower()
        if until > self._pair_locks.get(sym, 0.0):
            self._pair_locks[sym] = until
            self._lock_reasons[sym] = (
                f"cooldown: exited now, locked {self.cooldown_candles} candle(s)")
            self._save()

    def pair_locked(self, symbol: str, now: Optional[float] = None) -> tuple[bool, str]:
        now = now or time.time()
        until = self._pair_locks.get(symbol.lower(), 0.0)
        if until > now:
            mins = int((until - now) / 60)
            return True, f"{self._lock_reasons.get(symbol.lower(), 'locked')} ({mins} min remaining)"
        return False, ""

    def global_locked(self, now: Optional[float] = None) -> tuple[bool, str]:
        now = now or time.time()
        if self._global_lock_until > now:
            mins = int((self._global_lock_until - now) / 60)
            return True, f"{self._global_lock_reason} ({mins} min remaining)"
        return False, ""

    def can_enter(self, symbol: str, trades: Sequence[TradeRecord],
                  now: Optional[float] = None) -> tuple[bool, str]:
        """Single question the auto-trader asks before any buy."""
        now = now or time.time()
        # fast path: already-active locks (persisted from previous ticks)
        blocked, why = self.global_locked(now)
        if blocked:
            return False, why
        blocked, why = self.pair_locked(symbol, now)
        if blocked:
            return False, why
        hits = self.run_all(symbol, trades, now)
        if hits:
            worst = max(hits, key=lambda h: h.until)
            mins = max(0, int((worst.until - now) / 60))
            return False, f"{worst.reason} ({mins} min remaining)"
        return True, "ok"

    def status(self, now: Optional[float] = None) -> dict:
        now = now or time.time()
        active_pairs = {s: u for s, u in self._pair_locks.items() if u > now}
        return {
            "global_lock": self._global_lock_until > now,
            "global_reason": self._global_lock_reason if self._global_lock_until > now else "",
            "global_unlock_in_min": max(0, int((self._global_lock_until - now) / 60)),
            "pair_locks": {s: int((u - now) / 60) for s, u in active_pairs.items()},
        }


# ------------------------------------------------------------------ utils --

def trades_from_portfolio(pf) -> list[TradeRecord]:
    """Convert portfolio.Trade log into FIFO-matched closed TradeRecords.

    A sell closes whatever buy lots it consumes; each consumed lot becomes one
    TradeRecord with realized profit vs that lot's cost basis (same matching
    logic as risk.closed_trade_stats, but per-lot so guards see real exits).
    """
    from collections import defaultdict, deque

    lots: dict[str, deque] = defaultdict(deque)
    records: list[TradeRecord] = []
    for t in getattr(pf, "trades", []):
        side = getattr(t, "side", "")
        sym = getattr(t, "symbol", "").lower()
        px = float(getattr(t, "price", 0) or 0)
        usd = float(getattr(t, "usd_value", 0) or 0)
        amt = float(getattr(t, "amount", 0) or 0)
        ts = _ts_of(getattr(t, "timestamp", getattr(t, "time", getattr(t, "ts", ""))))
        tag = _tag_from_note(getattr(t, "note", ""))
        if side == "buy":
            cost = usd / amt if amt > 0 else px
            lots[sym].append([cost, amt, ts])
        elif side == "sell":
            remaining = amt if amt > 0 else (usd / px if px else 0)
            q = lots[sym]
            while remaining > 1e-12 and q:
                lot = q[0]
                take = min(lot[1], remaining)
                if lot[0] > 0 and px > 0:
                    profit = (px - lot[0]) / lot[0] * 100.0
                    records.append(TradeRecord(sym, ts, profit, tag))
                lot[1] -= take
                remaining -= take
                if lot[1] <= 1e-12:
                    q.popleft()
    return records


def _ts_of(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return time.time()


_TAG_HINTS = ("stop", "hard stop", "trailing", "chandelier", "take profit", "tp1",
              "time stop", "defensive", "mean-rev", "breakdown")


def _tag_from_note(note: str) -> str:
    n = (note or "").lower()
    if "hard stop" in n:
        return "hard_stop"
    if "trailing stop" in n:
        return "trail_stop"
    if "chandelier" in n:
        return "chandelier"
    if "stop" in n and "time stop" not in n:
        return "stop"
    if "time stop" in n:
        return "time_stop"
    if "tp1" in n or "take profit" in n:
        return "take_profit"
    return "exit"


_protections: Optional[Protections] = None


def get_protections() -> Protections:
    global _protections
    if _protections is None:
        _protections = Protections()
    return _protections
