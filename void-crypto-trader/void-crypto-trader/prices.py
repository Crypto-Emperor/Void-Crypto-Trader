"""
Real-time price feeds with caching and fallbacks.
CoinGecko (no key) → Jupiter (optional key) → demo prices.
"""

from __future__ import annotations
import os
import time
from typing import Optional
import requests
from rich.console import Console

console = Console()

COINGECKO_IDS = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
    "usdc": "usd-coin",
    "usdt": "tether",
    "bonk": "bonk",
    "wif": "dogwifcoin",
    "jup": "jupiter-exchange-solana",
    "ray": "raydium",
    "pyth": "pyth-network",
    "render": "render-token",
    "jito": "jito-governance-token",
}

MINTS = {
    "sol": "So11111111111111111111111111111111111111112",
    "usdc": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "bonk": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "wif": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "jup": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
}

_DEMO = {
    "btc": 95000.0,
    "eth": 3400.0,
    "sol": 180.0,
    "bonk": 0.000025,
    "wif": 1.85,
    "jup": 0.85,
    "ray": 3.2,
}

_cache: dict[str, tuple[float, float]] = {}
CACHE_TTL = 20


def _get_cached(symbol: str) -> Optional[float]:
    e = _cache.get(symbol.lower())
    if e and (time.time() - e[1]) < CACHE_TTL:
        return e[0]
    return None


def _set_cache(symbol: str, price: float) -> None:
    _cache[symbol.lower()] = (price, time.time())


def get_price_coingecko(symbol: str) -> Optional[float]:
    symbol = symbol.lower().strip()
    if symbol in ("usd", "usdc", "usdt"):
        return 1.0
    cached = _get_cached(symbol)
    if cached is not None:
        return cached
    cg_id = COINGECKO_IDS.get(symbol, symbol)
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
            timeout=10,
        )
        r.raise_for_status()
        price = r.json().get(cg_id, {}).get("usd")
        if price is not None:
            _set_cache(symbol, float(price))
            return float(price)
    except Exception:
        pass
    return None


def get_price_jupiter(symbol: str) -> Optional[float]:
    symbol = symbol.lower().strip()
    if symbol in ("usd", "usdc", "usdt"):
        return 1.0
    mint = MINTS.get(symbol, symbol)
    cached = _get_cached(symbol)
    if cached is not None:
        return cached
    headers = {}
    key = os.getenv("JUPITER_API_KEY")
    if key:
        headers["x-api-key"] = key
    try:
        r = requests.get(
            f"https://api.jup.ag/price/v3?ids={mint}",
            headers=headers or None,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        item = data.get(mint) or (list(data.values())[0] if data else None)
        if isinstance(item, dict):
            price = item.get("usdPrice") or item.get("price")
            if price is not None:
                price = float(price)
                _set_cache(symbol, price)
                return price
    except Exception:
        pass
    return None


def get_price(symbol: str, source: str = "auto") -> Optional[float]:
    symbol = symbol.lower().strip()
    if symbol in ("usd", "usdc", "usdt"):
        return 1.0

    order = (
        [get_price_coingecko, get_price_jupiter]
        if source in ("auto", "coingecko")
        else [get_price_jupiter, get_price_coingecko]
    )
    for fn in order:
        p = fn(symbol)
        if p is not None:
            return p

    if symbol in _DEMO:
        return _DEMO[symbol]
    return None


def get_prices(symbols: list[str], source: str = "auto") -> dict[str, float]:
    out = {}
    for s in symbols:
        p = get_price(s, source)
        if p is not None:
            out[s.lower()] = p
    return out
