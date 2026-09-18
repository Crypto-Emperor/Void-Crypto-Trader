"""
Strict honeypot / sellability checks via GoPlus Security API.

Before buying newly launched or unknown on-chain tokens, we verify the
contract allows selling. Majors (BTC/ETH/SOL/stablecoins) are allow-listed.

GoPlus docs:
  Solana: GET https://api.gopluslabs.io/api/v1/solana/token_security?contract_addresses=
  EVM:    GET https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses=

Optional GOPLUS_API_KEY improves rate limits (free tier works without key at low volume).
"""

from __future__ import annotations
import os
import time
from dataclasses import dataclass
from typing import Optional
import requests
from rich.console import Console

console = Console()

# Symbols we never need to scan (not honeypot-style contracts in this bot)
SAFE_SYMBOLS = {
    "btc", "eth", "sol", "usdc", "usdt", "usd", "wbtc", "weth", "steth",
    "jup", "ray", "pyth", "jito", "render",
}

# Known mint → treat as safe major when price path uses mint
SAFE_MINTS = {
    "So11111111111111111111111111111111111111112",  # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}

# Map symbol → default chain for scan (solana-first for FOMO)
DEFAULT_CHAIN = "solana"

# GoPlus EVM chain ids
EVM_CHAIN_IDS = {
    "ethereum": "1",
    "eth": "1",
    "bsc": "56",
    "bnb": "56",
    "polygon": "137",
    "base": "8453",
    "arbitrum": "42161",
    "avalanche": "43114",
    "optimism": "10",
}

_cache: dict[str, tuple["SecurityResult", float]] = {}
CACHE_TTL = 300  # 5 min


@dataclass
class SecurityResult:
    ok: bool
    is_honeypot: bool
    can_sell: bool
    buy_tax: Optional[float]
    sell_tax: Optional[float]
    risk_notes: list[str]
    raw_summary: str
    source: str = "goplus"

    def blocked_reason(self) -> str:
        if self.is_honeypot:
            return "HONEYPOT: contract appears to block sells"
        if not self.can_sell:
            return "CANNOT SELL: scanner reports sell disabled / high risk"
        if self.sell_tax is not None and self.sell_tax >= 0.25:
            return f"SELL TAX too high ({self.sell_tax*100:.0f}%)"
        return "blocked by security policy"


def _headers() -> dict:
    h = {"Accept": "application/json"}
    key = os.getenv("GOPLUS_API_KEY")
    if key:
        # Some GoPlus setups use Bearer access token
        h["Authorization"] = f"Bearer {key}"
    return h


def _get_cached(key: str) -> Optional[SecurityResult]:
    e = _cache.get(key)
    if e and (time.time() - e[1]) < CACHE_TTL:
        return e[0]
    return None


def _set_cache(key: str, result: SecurityResult) -> None:
    _cache[key] = (result, time.time())


def check_solana_token(mint: str) -> SecurityResult:
    """GoPlus Solana token security (beta)."""
    mint = mint.strip()
    cache_key = f"sol:{mint}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    url = "https://api.gopluslabs.io/api/v1/solana/token_security"
    try:
        r = requests.get(
            url,
            params={"contract_addresses": mint},
            headers=_headers(),
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        # Typical shape: { code, message, result: { mint: { ... } } }
        result_map = data.get("result") or data.get("data") or {}
        info = result_map.get(mint) or result_map.get(mint.lower())
        if not info and isinstance(result_map, dict) and len(result_map) == 1:
            info = next(iter(result_map.values()))

        if not info:
            # Unknown to scanner — strict mode treats as fail-closed for new tokens
            res = SecurityResult(
                ok=False,
                is_honeypot=True,
                can_sell=False,
                buy_tax=None,
                sell_tax=None,
                risk_notes=["No GoPlus data for this mint (unknown / too new)"],
                raw_summary="no_data",
            )
            _set_cache(cache_key, res)
            return res

        # Field names vary slightly across GoPlus versions
        is_hp = _truthy(info.get("is_honeypot") or info.get("honeypot") or info.get("isHoneypot"))
        # Solana-specific: look for sellability / freeze / transfer hooks
        transferable = info.get("transferable")
        if transferable is not None and not _truthy(transferable):
            is_hp = True
        freezable = info.get("freezable") or info.get("is_freezable")
        notes = []
        if _truthy(freezable):
            notes.append("token can be frozen by authority")
        if _truthy(info.get("mintable") or info.get("is_mintable")):
            notes.append("mint authority still active")
        if _truthy(info.get("closable")):
            notes.append("account closable by authority")

        buy_tax = _to_float(info.get("buy_tax") or info.get("buyTax"))
        sell_tax = _to_float(info.get("sell_tax") or info.get("sellTax"))

        can_sell = not is_hp
        if sell_tax is not None and sell_tax >= 0.25:
            can_sell = False
            notes.append(f"sell tax {sell_tax*100:.0f}%")

        ok = can_sell and not is_hp
        res = SecurityResult(
            ok=ok,
            is_honeypot=bool(is_hp),
            can_sell=can_sell,
            buy_tax=buy_tax,
            sell_tax=sell_tax,
            risk_notes=notes,
            raw_summary=str(info)[:400],
        )
        _set_cache(cache_key, res)
        return res
    except Exception as e:
        # Fail-closed under strict mode
        res = SecurityResult(
            ok=False,
            is_honeypot=True,
            can_sell=False,
            buy_tax=None,
            sell_tax=None,
            risk_notes=[f"scanner error: {e}"],
            raw_summary="error",
        )
        return res


def check_evm_token(address: str, chain: str = "ethereum") -> SecurityResult:
    address = address.strip().lower()
    chain_id = EVM_CHAIN_IDS.get(chain.lower(), "1")
    cache_key = f"evm:{chain_id}:{address}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
    try:
        r = requests.get(
            url,
            params={"contract_addresses": address},
            headers=_headers(),
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        result_map = data.get("result") or {}
        info = result_map.get(address) or result_map.get(address.lower())
        if not info and result_map:
            info = next(iter(result_map.values()), None)

        if not info:
            res = SecurityResult(
                ok=False, is_honeypot=True, can_sell=False,
                buy_tax=None, sell_tax=None,
                risk_notes=["No GoPlus data (unknown token)"],
                raw_summary="no_data",
            )
            _set_cache(cache_key, res)
            return res

        is_hp = _truthy(info.get("is_honeypot"))
        cannot_buy = _truthy(info.get("cannot_buy"))
        cannot_sell_all = _truthy(info.get("cannot_sell_all"))
        buy_tax = _to_float(info.get("buy_tax"))
        sell_tax = _to_float(info.get("sell_tax"))

        notes = []
        if _truthy(info.get("is_open_source")) is False:
            notes.append("source not verified")
        if _truthy(info.get("is_proxy")):
            notes.append("proxy contract")
        if _truthy(info.get("is_mintable")):
            notes.append("mintable")
        if _truthy(info.get("owner_change_balance")):
            notes.append("owner can change balances")
        if _truthy(info.get("is_blacklisted")):
            notes.append("blacklist function")
        if cannot_sell_all:
            notes.append("cannot sell all")

        can_sell = not is_hp and not cannot_sell_all
        if sell_tax is not None and sell_tax >= 0.25:
            can_sell = False
            notes.append(f"sell tax {sell_tax*100:.0f}%")

        ok = can_sell and not is_hp and not cannot_buy
        res = SecurityResult(
            ok=ok,
            is_honeypot=bool(is_hp),
            can_sell=can_sell,
            buy_tax=buy_tax,
            sell_tax=sell_tax,
            risk_notes=notes,
            raw_summary=str(info)[:400],
        )
        _set_cache(cache_key, res)
        return res
    except Exception as e:
        return SecurityResult(
            ok=False, is_honeypot=True, can_sell=False,
            buy_tax=None, sell_tax=None,
            risk_notes=[f"scanner error: {e}"],
            raw_summary="error",
        )


def _truthy(v) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y")


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_safe_symbol(symbol: str) -> bool:
    return symbol.lower().strip() in SAFE_SYMBOLS


def looks_like_mint(s: str) -> bool:
    """Solana mints are base58 ~32-44 chars; EVM is 0x + 40 hex."""
    s = s.strip()
    if s.startswith("0x") and len(s) == 42:
        return True
    if 32 <= len(s) <= 44 and not s.lower() in SAFE_SYMBOLS:
        # rough base58 check
        return all(c.isalnum() for c in s)
    return False


def check_before_buy(
    symbol_or_mint: str,
    *,
    chain: str = "solana",
    strict: bool = True,
    skip_safe: bool = True,
) -> tuple[bool, str, Optional[SecurityResult]]:
    """
    Gate for execute_buy.
    Returns (allowed, message, result).
    strict=True → fail closed on scanner errors / unknown tokens.
    """
    key = symbol_or_mint.strip()
    lower = key.lower()

    if skip_safe and (is_safe_symbol(lower) or key in SAFE_MINTS):
        return True, "allow-listed major", None

    # Resolve mint if we only have a symbol that is not in SAFE — strategies
    # often pass "bonk" etc. Majors already allowed; for unknowns require mint.
    from prices import MINTS  # local known map

    mint = MINTS.get(lower, key if looks_like_mint(key) else None)
    if mint is None:
        if strict:
            return False, (
                f"Refusing buy of '{key}': not allow-listed and no mint address. "
                "Pass a contract/mint address for honeypot scan."
            ), None
        return True, "no mint — skipped scan (non-strict)", None

    if chain.lower() in ("solana", "sol"):
        result = check_solana_token(mint)
    else:
        result = check_evm_token(mint, chain)

    if result.ok:
        note = "passed honeypot/sell check"
        if result.risk_notes:
            note += f" (notes: {', '.join(result.risk_notes)})"
        return True, note, result

    reason = result.blocked_reason()
    if result.risk_notes:
        reason += " | " + "; ".join(result.risk_notes)
    return False, reason, result
