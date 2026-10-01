"""
Executor router: decides HOW a live order hits the market.

Priority:
  1. Jupiter/Phantom on-chain swaps (executor.jupiter) - fast, reliable, no browser.
     Used whenever we can resolve the symbol to a Solana mint (or it IS a mint).
  2. FOMO web-UI automation (executor.live_fomo) - legacy fallback for symbols
     only tradeable through the FOMO app.

Symbol -> mint resolution order:
  - looks like a base58 mint already -> use directly
  - memecoin.resolve_pool(symbol).token_address (DexScreener/GeckoTerminal)
  - known majors map (sol/eth/btc...)
"""

from __future__ import annotations

from typing import Optional

MAJOR_MINTS = {
    "sol": "So11111111111111111111111111111111111111112",
    "wsol": "So11111111111111111111111111111111111111112",
    "usdc": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "usdt": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "bonk": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "wif": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "jup": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "ray": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "render": "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
    "pyth": "Hne4S6XeotpsTx6pWzPQpiRHzzY5dAwNDpwrgH5f5k4A",
}


def _looks_like_mint(s: str) -> bool:
    if len(s) < 32 or len(s) > 44:
        return False
    try:
        from security import looks_like_mint
        return looks_like_mint(s)
    except Exception:
        import string
        b58 = set(string.ascii_letters + string.digits) - set("lIO0")
        return all(c in b58 for c in s)


def resolve_mint(symbol: str) -> Optional[str]:
    """Map a trading symbol to a Solana token mint, or None."""
    s = (symbol or "").strip().lower()
    if not s:
        return None
    if s in MAJOR_MINTS:
        return MAJOR_MINTS[s]
    if _looks_like_mint(symbol.strip()):
        return symbol.strip()
    # DEX search (works for trending memes: new tickers not in our static map)
    try:
        from memecoin import resolve_pool
        pool = resolve_pool(s, network="solana")
        if pool and getattr(pool, "token_address", ""):
            addr = pool.token_address
            if _looks_like_mint(addr):
                return addr
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------

def route_live_buy(symbol: str, usd_amount: float) -> tuple[bool, str]:
    mint = resolve_mint(symbol)
    if mint:
        try:
            from executor.jupiter import live_buy as jlive_buy
            return jlive_buy(mint, usd_amount)
        except RuntimeError as e:
            return False, f"jupiter executor unavailable: {e}"
        except Exception as e:
            return False, f"jupiter executor error: {e}"
    # No mint - legacy FOMO UI path
    try:
        from executor.live_fomo import live_buy as fomo_buy
        return fomo_buy(symbol, usd_amount)
    except Exception as e:
        return False, f"FOMO executor error: {e}"


def route_live_sell(symbol: str, token_amount: float) -> tuple[bool, str]:
    """token_amount is in human-readable tokens (portfolio units)."""
    mint = resolve_mint(symbol)
    if mint:
        try:
            decimals = _token_decimals(mint)
            raw = int(token_amount * (10 ** decimals))
            from executor.jupiter import live_sell as jlive_sell
            return jlive_sell(mint, raw)
        except Exception as e:
            return False, f"jupiter sell error: {e}"
    try:
        from executor.live_fomo import live_sell as fomo_sell
        return fomo_sell(symbol, token_amount)
    except Exception as e:
        return False, f"FOMO executor error: {e}"


_DECIMALS_CACHE: dict[str, int] = {"So11111111111111111111111111111111111111112": 9,
                                  "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 6,
                                  "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": 6}


def _token_decimals(mint: str) -> int:
    if mint in _DECIMALS_CACHE:
        return _DECIMALS_CACHE[mint]
    try:
        from executor.jupiter import rpc
        res = rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        info = (res or {}).get("value", {}).get("data", {}).get("parsed", {}).get("info", {})
        dec = int(info.get("decimals", 9))
        _DECIMALS_CACHE[mint] = dec
        return dec
    except Exception:
        return 9  # most SPL mints are 9; worst case sells slightly less than held
