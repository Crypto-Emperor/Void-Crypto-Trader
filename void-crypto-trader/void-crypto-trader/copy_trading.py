"""
FOMO Copy Trading engine (better than simple leaderboard consensus).

What makes this stronger:
1. Follow specific FOMO handles (not only top-N token overlap)
2. Poll real trader trades + balances via fomoapi.io
3. Detect NEW opens / closes vs last-seen state (deduped)
4. Sizing modes: fixed_usd | pct_equity | scale_leader
5. Per-account follow lists (works with multi-account <=5)
6. Still gated by honeypot checks on every buy
7. Cooldown + max concurrent copied positions

Endpoints used (FOMOAPI):
  GET /v2/users/{handle}/trades
  GET /v2/users/{handle}/balances
  GET /v2/leaderboard/{window}  (optional auto-pick leaders)
"""

from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
import requests
from rich.console import Console

from config import BASE_DIR, settings

console = Console()
_TRADES_AUTH_WARNED = False
FOMOAPI_BASE = os.getenv("FOMOAPI_BASE", "https://api.fomoapi.io")
STATE_FILE = BASE_DIR / "copy_state.json"
FOLLOWS_FILE = BASE_DIR / "copy_follows.json"


def _headers() -> dict:
    h = {"Accept": "application/json"}
    key = os.getenv("FOMOAPI_KEY") or os.getenv("FOMO_API_KEY")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


# ---------- Follow list (global + can be scoped per account id) ----------

@dataclass
class FollowConfig:
    handle: str
    enabled: bool = True
    # optional overrides
    max_usd_per_trade: Optional[float] = None
    account_ids: list[str] = field(default_factory=list)  # empty = all accounts


def load_follows() -> list[FollowConfig]:
    if not FOLLOWS_FILE.exists():
        return []
    try:
        data = json.loads(FOLLOWS_FILE.read_text())
        out = []
        for row in data.get("follows", []):
            out.append(
                FollowConfig(
                    handle=str(row["handle"]).lstrip("@").lower(),
                    enabled=bool(row.get("enabled", True)),
                    max_usd_per_trade=row.get("max_usd_per_trade"),
                    account_ids=[str(x) for x in row.get("account_ids", [])],
                )
            )
        return out
    except Exception:
        return []


def save_follows(follows: list[FollowConfig]) -> None:
    FOLLOWS_FILE.write_text(
        json.dumps({"follows": [asdict(f) for f in follows]}, indent=2)
    )


def follow_trader(handle: str, account_ids: list[str] | None = None) -> FollowConfig:
    handle = handle.lstrip("@").lower()
    follows = load_follows()
    for f in follows:
        if f.handle == handle:
            f.enabled = True
            if account_ids is not None:
                f.account_ids = account_ids
            save_follows(follows)
            return f
    cfg = FollowConfig(handle=handle, account_ids=account_ids or [])
    follows.append(cfg)
    save_follows(follows)
    return cfg


def unfollow_trader(handle: str) -> bool:
    handle = handle.lstrip("@").lower()
    follows = load_follows()
    new = [f for f in follows if f.handle != handle]
    if len(new) == len(follows):
        return False
    save_follows(new)
    return True


# ---------- API: trades & balances ----------

@dataclass
class LeaderTrade:
    trade_id: str
    handle: str
    symbol: str
    mint: Optional[str]
    status: str  # open | closed
    side_hint: str  # buy | sell inferred
    usd_value: float
    created_at: str
    closed_at: Optional[str] = None


def fetch_user_trades(handle: str, limit: int = 30) -> list[LeaderTrade]:
    handle = handle.lstrip("@")
    # Free layer (DexScreener) - no fomoapi credits
    try:
        from free_api import data_source, free_trades_for_handle
        if data_source() == "free":
            raw = free_trades_for_handle(handle, limit=limit)
            return [
                LeaderTrade(
                    trade_id=x.trade_id,
                    handle=x.handle,
                    symbol=x.symbol,
                    mint=x.mint,
                    status=x.status,
                    side_hint=x.side_hint,
                    usd_value=x.usd_value,
                    created_at=x.created_at,
                )
                for x in raw
            ]
    except Exception:
        pass
    url = f"{FOMOAPI_BASE}/v2/users/{handle}/trades"
    try:
        r = requests.get(url, params={"limit": limit}, headers=_headers(), timeout=15)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        data = r.json()
        if data.get("available") is False:
            return []
        rows = data.get("trades") or data.get("data") or []
        out: list[LeaderTrade] = []
        for row in rows:
            token = row.get("token") or {}
            symbol = (token.get("symbol") or row.get("symbol") or "unknown").lower()
            mint = token.get("address") or row.get("address")
            status = (row.get("status") or "open").lower()
            tid = str(row.get("tradeId") or row.get("id") or f"{handle}:{symbol}:{row.get('createdAt')}")
            # estimate USD notional
            amt = float(row.get("amount") or 0)
            entry = float(row.get("avgEntryPrice") or row.get("price") or 0)
            usd = float(row.get("valueUsd") or (amt * entry if entry else 0) or 0)
            side = "sell" if status == "closed" else "buy"
            if row.get("side"):
                side = str(row["side"]).lower()
            out.append(
                LeaderTrade(
                    trade_id=tid,
                    handle=handle.lower(),
                    symbol=symbol,
                    mint=mint,
                    status=status,
                    side_hint=side,
                    usd_value=usd,
                    created_at=str(row.get("createdAt") or ""),
                    closed_at=row.get("closedAt"),
                )
            )
        return out
    except Exception as e:
        global _TRADES_AUTH_WARNED
        msg = str(e)
        if "401" in msg:
            if not _TRADES_AUTH_WARNED:
                console.print(
                    "[yellow]FOMOAPI trades need FOMOAPI_KEY "
                    "(https://fomoapi.io) - using demo trades until set[/yellow]"
                )
                _TRADES_AUTH_WARNED = True
        else:
            console.print(f"[dim yellow]copy trades @{handle}: {e}[/dim yellow]")
        return _demo_trades(handle)

def _demo_trades(handle: str) -> list[LeaderTrade]:
    """Offline demo so copy strategies work without FOMOAPI_KEY."""
    # rotate symbols by handle hash so different top accounts buy different things
    pool = [
        ("sol", "open", 200.0),
        ("eth", "open", 180.0),
        ("bonk", "open", 90.0),
        ("wif", "open", 75.0),
        ("btc", "open", 250.0),
        ("jup", "open", 60.0),
    ]
    h = sum(ord(c) for c in handle) % len(pool)
    # each handle "buys" 2 tokens; one handle occasionally sells sol
    picks = [pool[h], pool[(h + 2) % len(pool)]]
    out = []
    for i, (sym, status, usd) in enumerate(picks):
        out.append(
            LeaderTrade(
                trade_id=f"demo:{handle}:{sym}:{status}:{i}",
                handle=handle.lower(),
                symbol=sym,
                mint=None,
                status=status,
                side_hint="buy" if status == "open" else "sell",
                usd_value=usd,
                created_at="",
            )
        )
    return out


def fetch_user_balances(handle: str) -> list[dict]:
    handle = handle.lstrip("@")
    url = f"{FOMOAPI_BASE}/v2/users/{handle}/balances"
    try:
        r = requests.get(url, headers=_headers(), timeout=15)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        data = r.json()
        if data.get("available") is False:
            return []
        return data.get("holdings") or data.get("balances") or data.get("data") or []
    except Exception as e:
        console.print(f"[dim yellow]copy balances @{handle}: {e}[/dim yellow]")
        return []


# ---------- Persistent copy state (dedup) ----------

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"seen_trade_ids": [], "last_open": {}}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"seen_trade_ids": [], "last_open": {}}


def _save_state(state: dict) -> None:
    # keep seen list bounded
    seen = state.get("seen_trade_ids") or []
    state["seen_trade_ids"] = seen[-2000:]
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass  # disk/sandbox issues should not kill the trader


def mark_seen(trade_id: str) -> None:
    st = _load_state()
    if trade_id not in st["seen_trade_ids"]:
        st["seen_trade_ids"].append(trade_id)
        _save_state(st)


def already_seen(trade_id: str) -> bool:
    return trade_id in set(_load_state().get("seen_trade_ids") or [])


# ---------- Signal generation for strategy ----------

@dataclass
class CopySignal:
    action: str  # buy | sell
    symbol: str
    mint: Optional[str]
    usd_amount: Optional[float]
    amount: Optional[str]  # for sells e.g. "100%"
    reason: str
    leader: str
    trade_id: str


def compute_size(
    leader_usd: float,
    follower_equity: float,
    follower_cash: float,
    mode: str,
    fixed_usd: float,
    pct_equity: float,
    scale: float,
    max_usd: float,
    *,
    conviction: int = 1,
    leader_pnl: float = 0.0,
    leader_rank: int = 10,
    min_usd: float = 10.0,
) -> float:
    """
    Decide how much USD to allocate to one token.

    Modes:
      smart (default)  - blend of equity %, leader size, conviction, rank
      fixed_usd        - always fixed_usd
      pct_equity       - pct of your equity
      scale_leader     - leader_usd * scale

    smart formula (capped):
      base = equity * (pct_equity/100)   OR fixed_usd if equity small
      * conviction_mult (1.0 -> 1.6 as more UP traders buy same token)
      * rank_mult       (top ranks get a bit more)
      * leader_size_tilt (slightly more if leaders put real size in)
      then clamp to [min_usd, max_usd, 75% cash]
    """
    mode = (mode or "smart").lower()
    conviction = max(1, int(conviction))
    leader_rank = max(1, int(leader_rank))

    if mode == "fixed_usd":
        size = fixed_usd
    elif mode == "pct_equity":
        size = follower_equity * (pct_equity / 100.0)
    elif mode == "scale_leader":
        size = max(leader_usd, 0) * scale
    else:
        # --- smart ---
        base = follower_equity * (pct_equity / 100.0)
        if base < fixed_usd * 0.5:
            base = fixed_usd
        # conviction: 1 trader=1.0, 2=1.2, 3=1.35, 4+=1.5-1.6
        conv_mult = min(1.0 + 0.2 * (conviction - 1), 1.6)
        # rank: rank 1 -> 1.25, rank 10 -> ~1.0
        rank_mult = max(1.0, 1.3 - 0.03 * (leader_rank - 1))
        # leader notional tilt: log-ish, don't explode on whale buys
        if leader_usd > 0:
            tilt = min(1.0 + (leader_usd / 5000.0) * 0.15, 1.35)
        else:
            tilt = 1.0
        # mild boost if leader PnL is very strong
        pnl_mult = 1.0
        if leader_pnl >= 100_000:
            pnl_mult = 1.1
        elif leader_pnl >= 25_000:
            pnl_mult = 1.05
        size = base * conv_mult * rank_mult * tilt * pnl_mult

    # Hard rule: never more than max_position_pct of equity (default 6%)
    try:
        from config import settings
        pct_cap = float(getattr(settings, "max_position_pct", 6) or 6) / 100.0
    except Exception:
        pct_cap = float(os.getenv("MAX_POSITION_PCT", "6")) / 100.0
    equity_cap = follower_equity * pct_cap
    size = min(size, max_usd, follower_cash * 0.75, equity_cap)
    if size < min_usd:
        return 0.0
    return round(size, 2)


def collect_copy_signals(
    account_id: str,
    follower_equity: float,
    follower_cash: float,
    open_position_symbols: set[str],
) -> list[CopySignal]:
    """
    For one follower account, scan all enabled follows and emit new copy signals.
    """
    mode = os.getenv("COPY_SIZE_MODE", "fixed_usd")
    fixed_usd = float(os.getenv("COPY_FIXED_USD", str(settings.trade_usd)))
    pct_equity = float(os.getenv("COPY_PCT_EQUITY", "5"))
    scale = float(os.getenv("COPY_SCALE", "0.1"))
    max_usd = float(os.getenv("COPY_MAX_USD", str(settings.live_max_usd if settings.is_live else settings.trade_usd * 3)))
    max_positions = int(os.getenv("COPY_MAX_POSITIONS", "8"))

    signals: list[CopySignal] = []
    follows = [f for f in load_follows() if f.enabled]
    if not follows:
        return []

    for fc in follows:
        # account scope
        if fc.account_ids and account_id not in fc.account_ids:
            continue
        trades = fetch_user_trades(fc.handle, limit=20)
        per_max = fc.max_usd_per_trade or max_usd

        for tr in trades:
            if already_seen(tr.trade_id):
                continue
            sym = tr.symbol.lower()
            if sym in ("usdc", "usdt", "usd", "unknown"):
                mark_seen(tr.trade_id)
                continue

            if tr.status == "open" or tr.side_hint == "buy":
                if len(open_position_symbols) + len([s for s in signals if s.action == "buy"]) >= max_positions:
                    continue
                size = compute_size(
                    tr.usd_value, follower_equity, follower_cash,
                    mode, fixed_usd, pct_equity, scale, per_max,
                )
                if size < 5:
                    mark_seen(tr.trade_id)
                    continue
                signals.append(
                    CopySignal(
                        action="buy",
                        symbol=sym,
                        mint=tr.mint,
                        usd_amount=size,
                        amount=None,
                        reason=f"copy @{fc.handle} OPEN {sym}",
                        leader=fc.handle,
                        trade_id=tr.trade_id,
                    )
                )
            elif tr.status == "closed" or tr.side_hint == "sell":
                if sym in open_position_symbols:
                    signals.append(
                        CopySignal(
                            action="sell",
                            symbol=sym,
                            mint=tr.mint,
                            usd_amount=None,
                            amount="100%",
                            reason=f"copy @{fc.handle} CLOSE {sym}",
                            leader=fc.handle,
                            trade_id=tr.trade_id,
                        )
                    )
                else:
                    mark_seen(tr.trade_id)

    return signals
