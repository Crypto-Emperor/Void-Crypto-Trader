"""
FOMO token universe — what FOMO users are actually trading.

Requires FOMOAPI_KEY (https://fomoapi.io/dashboard).

Boards (official FOMO API):
  GET /v2/leaderboard/tokens/trending
  GET /v2/leaderboard/tokens/most-held
  GET /v2/leaderboard/tokens/graduated

Without a key there is no honest "every memecoin on FOMO" list.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional

from http_util import request_with_backoff

FOMOAPI_BASE = os.getenv("FOMOAPI_BASE", "https://api.fomoapi.io")
_cache: dict[str, tuple[list, float]] = {}
CACHE_TTL = float(os.getenv("FOMO_TOKEN_CACHE_SEC", "45"))


@dataclass
class FomoToken:
    rank: int
    symbol: str
    address: str
    name: str = ""
    network: str = "solana"
    price_usd: float = 0.0
    change_24h: float = 0.0
    market_cap_usd: float = 0.0
    holders: int = 0
    board: str = ""


def _headers() -> dict:
    h = {"Accept": "application/json"}
    key = os.getenv("FOMOAPI_KEY") or os.getenv("FOMO_API_KEY")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def has_fomo_key() -> bool:
    return bool(os.getenv("FOMOAPI_KEY") or os.getenv("FOMO_API_KEY"))


def _parse_tokens(data: dict, board: str, limit: int) -> list[FomoToken]:
    rows = data.get("tokens") or data.get("data") or data.get("result") or []
    out: list[FomoToken] = []
    for i, row in enumerate(rows[:limit]):
        if not isinstance(row, dict):
            continue
        tok = row.get("token") if isinstance(row.get("token"), dict) else row
        sym = (tok.get("symbol") or row.get("symbol") or "").strip()
        addr = (
            tok.get("address")
            or row.get("address")
            or tok.get("mint")
            or row.get("mint")
            or ""
        )
        if not sym and not addr:
            continue
        out.append(
            FomoToken(
                rank=int(row.get("rank") or i + 1),
                symbol=sym.lower() or (addr[:6] if addr else f"tok{i}"),
                address=str(addr),
                name=str(tok.get("name") or row.get("name") or sym),
                network=str(row.get("network") or tok.get("network") or "solana").lower(),
                price_usd=float(row.get("priceUsd") or row.get("price_usd") or tok.get("priceUsd") or 0),
                change_24h=float(row.get("change24h") or row.get("change_24h") or 0),
                market_cap_usd=float(row.get("marketCapUsd") or row.get("market_cap_usd") or 0),
                holders=int(row.get("holders") or row.get("holder_count") or 0),
                board=board,
            )
        )
    return out


def fetch_token_board(board: str = "trending", limit: int = 50) -> list[FomoToken]:
    """
    board: trending | most-held | graduated
    """
    board = board.lower().replace("_", "-")
    if board in ("mostheld", "most-held", "held"):
        path = "most-held"
        board = "most-held"
    elif board in ("graduated", "grad"):
        path = "graduated"
        board = "graduated"
    else:
        path = "trending"
        board = "trending"

    if not has_fomo_key():
        return []

    cache_key = f"{board}:{limit}"
    hit = _cache.get(cache_key)
    if hit and (time.time() - hit[1]) < CACHE_TTL:
        return hit[0]

    url = f"{FOMOAPI_BASE}/v2/leaderboard/tokens/{path}"
    try:
        r = request_with_backoff(
            "GET",
            url,
            params={"limit": limit},
            headers=_headers(),
            timeout=12,
            max_retries=2,
        )
        if r.status_code in (401, 403):
            return []
        if r.status_code == 429:
            return hit[0] if hit else []
        r.raise_for_status()
        data = r.json() if r is not None else {}
        if not isinstance(data, dict):
            return []
        tokens = _parse_tokens(data, board, limit)
        _cache[cache_key] = (tokens, time.time())
        return tokens
    except Exception:
        return hit[0] if hit else []


def fomo_memecoin_universe(limit: int = 80) -> list[FomoToken]:
    """
    Merge trending + most-held + graduated = FOMO's live memecoin surface.
    Deduped by address/symbol. This is the closest to 'every memecoin on FOMO'
    the public API exposes (boards, not infinite pump.fun catalog).
    """
    if not has_fomo_key():
        return []

    seen: set[str] = set()
    merged: list[FomoToken] = []
    for board in ("trending", "most-held", "graduated"):
        for t in fetch_token_board(board, limit=min(50, limit)):
            key = (t.address or t.symbol).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(t)
            if len(merged) >= limit:
                return merged
    return merged


def fomo_universe_as_hot_rows(limit: int = 80) -> list[dict]:
    """Shape compatible with free_hot_tokens / strategy auto-hot path."""
    rows = []
    for t in fomo_memecoin_universe(limit=limit):
        rows.append(
            {
                "symbol": t.symbol,
                "mint": t.address,
                "address": t.address,
                "price_usd": t.price_usd,
                "volume_h24": max(float(t.market_cap_usd) * 0.05, 1000.0),
                "change_h24": t.change_24h,
                "change_1h": 0.0,
                "source": f"fomo_{t.board}",
                "network": t.network,
            }
        )
    return rows
