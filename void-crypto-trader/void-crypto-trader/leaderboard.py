"""
FOMO leaderboard tracking.

Primary: fomoapi.io  GET /v2/leaderboard/{window}
  (optional FOMOAPI_KEY - free tier exists)

Fallback: synthetic demo board so strategies still run offline.
"""

from __future__ import annotations
import os
import time
from dataclasses import dataclass, field
from typing import Optional
import requests
from rich.console import Console

console = Console()

FOMOAPI_BASE = os.getenv("FOMOAPI_BASE", "https://api.fomoapi.io")
_cache: dict[str, tuple[list, float]] = {}
CACHE_TTL = 30  # nearer real-time


@dataclass
class LeaderboardTrader:
    rank: int
    handle: str
    pnl_usd: float = 0.0
    volume_usd: float = 0.0
    trades: int = 0
    followers: int = 0
    holdings: int = 0
    top_tokens: list[str] = field(default_factory=list)
    wallet_sol: Optional[str] = None
    wallet_evm: Optional[str] = None
    verified: bool = False


def _headers() -> dict:
    h = {"Accept": "application/json"}
    key = os.getenv("FOMOAPI_KEY") or os.getenv("FOMO_API_KEY")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def fetch_leaderboard(window: str = "24h", limit: int = 25) -> list[LeaderboardTrader]:
    window = window.lower()
    if window not in ("24h", "7d", "30d", "all"):
        window = "24h"
    cache_key = f"{window}:{limit}"
    hit = _cache.get(cache_key)
    if hit and (time.time() - hit[1]) < CACHE_TTL:
        return hit[0]

    # Prefer free DexScreener layer when no key / DATA_SOURCE=free
    use_free = False
    try:
        from free_api import data_source, free_leaderboard
        src = data_source()
        use_free = src == "free"
    except Exception:
        free_leaderboard = None  # type: ignore
        src = "demo"

    traders: list[LeaderboardTrader] = []
    if use_free and free_leaderboard:
        raw = free_leaderboard(window, limit)
        traders = [
            LeaderboardTrader(
                rank=x.rank,
                handle=x.handle,
                pnl_usd=x.pnl_usd,
                volume_usd=x.volume_usd,
                trades=x.trades,
                top_tokens=list(x.top_tokens),
                verified=x.verified,
            )
            for x in raw
        ]
        if traders:
            console.print("[dim]Leaderboard: free DexScreener feed (no fomoapi credits)[/dim]")
    if not traders:
        traders = _fetch_fomoapi(window, limit)
    if not traders:
        traders = _demo_leaderboard(limit)
        console.print("[dim]Leaderboard: demo fallback[/dim]")

    _cache[cache_key] = (traders, time.time())
    return traders


def _fetch_fomoapi(window: str, limit: int) -> list[LeaderboardTrader]:
    url = f"{FOMOAPI_BASE}/v2/leaderboard/{window}"
    try:
        r = requests.get(url, params={"limit": limit}, headers=_headers(), timeout=15)
        if r.status_code == 401:
            console.print("[yellow]FOMOAPI: unauthorized - add FOMOAPI_KEY to .env[/yellow]")
            return []
        r.raise_for_status()
        data = r.json()
        rows = data.get("traders") or data.get("data") or []
        out: list[LeaderboardTrader] = []
        for i, row in enumerate(rows[:limit]):
            wallets = row.get("wallets") or {}
            top = row.get("topTokens") or row.get("top_tokens") or []
            if isinstance(top, str):
                top = [top]
            out.append(
                LeaderboardTrader(
                    rank=int(row.get("rank") or i + 1),
                    handle=str(row.get("handle") or row.get("username") or f"trader{i}"),
                    pnl_usd=float(row.get("pnlUsd") or row.get("pnl") or 0),
                    volume_usd=float(row.get("volumeUsd") or row.get("volume") or 0),
                    trades=int(row.get("trades") or 0),
                    followers=int(row.get("followers") or 0),
                    holdings=int(row.get("holdings") or 0),
                    top_tokens=[str(t) for t in top if t],
                    wallet_sol=wallets.get("solana") or row.get("solana_address"),
                    wallet_evm=wallets.get("evm") or row.get("base_address"),
                    verified=bool(row.get("verified", True)),
                )
            )
        return out
    except Exception as e:
        console.print(f"[dim yellow]FOMOAPI leaderboard error: {e}[/dim yellow]")
        return []


def _demo_leaderboard(limit: int) -> list[LeaderboardTrader]:
    """Offline-friendly sample so the strategy can still generate signals."""
    sample = [
        ("alpha_whale", 120000, ["sol", "bonk", "wif"]),
        ("memeking", 85000, ["bonk", "wif", "jup"]),
        ("sol_sniper", 62000, ["sol", "jup"]),
        ("degen_lord", 41000, ["wif", "bonk"]),
        ("steady_gains", 28000, ["sol", "eth", "btc"]),
        ("base_hunter", 19000, ["eth", "btc"]),
        ("ray_rider", 15000, ["ray", "jup", "sol"]),
        ("quiet_pnl", 11000, ["btc", "eth"]),
    ]
    out = []
    for i, (handle, pnl, tokens) in enumerate(sample[:limit]):
        out.append(
            LeaderboardTrader(
                rank=i + 1,
                handle=handle,
                pnl_usd=float(pnl),
                volume_usd=pnl * 3,
                trades=50 + i * 10,
                followers=1000 * (8 - i),
                holdings=len(tokens),
                top_tokens=tokens,
                verified=True,
            )
        )
    return out


def top_token_consensus(
    window: str = "24h",
    top_n_traders: int = 10,
    min_mentions: int = 2,
) -> dict[str, int]:
    """
    Count how many top traders list each token in top_tokens.
    Returns symbol/mint -> mention count (sorted by strength implicitly by caller).
    """
    board = fetch_leaderboard(window, limit=top_n_traders)
    counts: dict[str, int] = {}
    for t in board[:top_n_traders]:
        for tok in t.top_tokens:
            key = tok.lower().strip()
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
    return {k: v for k, v in counts.items() if v >= min_mentions}


def format_leaderboard(window: str = "24h", limit: int = 15) -> str:
    rows = fetch_leaderboard(window, limit)
    lines = [f"FOMO leaderboard ({window}) - top {len(rows)}"]
    for t in rows:
        toks = ",".join(t.top_tokens[:3]) if t.top_tokens else "-"
        lines.append(
            f"  #{t.rank:<3} @{t.handle:<20} PnL ${t.pnl_usd:>10,.0f}  "
            f"holds={t.holdings}  tokens=[{toks}]"
        )
    return "\n".join(lines)


def traders_who_are_up(
    window: str = "24h",
    limit: int = 25,
    min_pnl: float = 0.0,
) -> list[LeaderboardTrader]:
    """
    Real-time-ish scan: leaderboard filtered to traders currently UP (PnL > min_pnl).
    Sorted by PnL descending.
    """
    board = fetch_leaderboard(window, limit=max(limit * 2, 30))
    up = [t for t in board if t.pnl_usd > min_pnl]
    up.sort(key=lambda x: x.pnl_usd, reverse=True)
    return up[:limit]
