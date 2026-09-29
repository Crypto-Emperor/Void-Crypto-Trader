"""
API / RPC provider resolution.

Fill any keys in keys/api_keys.env. The bot picks the first available endpoint.
Paid keys later: just paste them — no code change required.
"""

from __future__ import annotations

import os
from typing import Optional


def _env(*names: str) -> Optional[str]:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return None


def helius_rpc_url() -> Optional[str]:
    # Full URL wins; else build from API key
    url = _env("HELIUS_RPC_URL", "HELIUS_HTTP_URL")
    if url:
        return url
    key = _env("HELIUS_API_KEY")
    if key:
        return f"https://mainnet.helius-rpc.com/?api-key={key}"
    return None


def quicknode_rpc_url() -> Optional[str]:
    return _env("QUICKNODE_RPC_URL", "QUICKNODE_HTTP_URL")


def alchemy_rpc_url() -> Optional[str]:
    url = _env("ALCHEMY_RPC_URL", "ALCHEMY_SOLANA_URL")
    if url:
        return url
    key = _env("ALCHEMY_API_KEY")
    if key:
        # Solana mainnet template
        return f"https://solana-mainnet.g.alchemy.com/v2/{key}"
    return None


def chainstack_rpc_url() -> Optional[str]:
    return _env("CHAINSTACK_RPC_URL", "CHAINSTACK_HTTPS_URL")


def triton_rpc_url() -> Optional[str]:
    return _env("TRITON_RPC_URL", "TRITON_HTTP_URL")


def custom_rpc_url() -> Optional[str]:
    return _env("RPC_HTTP_URL", "SOLANA_RPC_URL", "SOLANA_HTTP_URL")


def resolve_solana_rpc_url() -> str:
    """
    Priority (first hit wins):
      1) RPC_HTTP_URL / SOLANA_RPC_URL (manual full URL — paid or free)
      2) Helius
      3) QuickNode
      4) Alchemy
      5) Chainstack
      6) Triton
      7) Public Solana RPC (slow / rate-limited)
    """
    for fn in (
        custom_rpc_url,
        helius_rpc_url,
        quicknode_rpc_url,
        alchemy_rpc_url,
        chainstack_rpc_url,
        triton_rpc_url,
    ):
        u = fn()
        if u:
            return u
    return "https://api.mainnet-beta.solana.com"


def active_rpc_provider_name(url: Optional[str] = None) -> str:
    u = (url or resolve_solana_rpc_url()).lower()
    if "helius" in u:
        return "helius"
    if "quicknode" in u or "quiknode" in u:
        return "quicknode"
    if "alchemy" in u:
        return "alchemy"
    if "chainstack" in u:
        return "chainstack"
    if "triton" in u or "rpcpool" in u:
        return "triton"
    if "mainnet-beta.solana.com" in u:
        return "public"
    return "custom"


def provider_status() -> dict:
    """For status / debugging — which keys are set (not the secret values)."""
    return {
        "solana_rpc": active_rpc_provider_name(),
        "rpc_url_set": bool(custom_rpc_url() or helius_rpc_url() or quicknode_rpc_url()
                            or alchemy_rpc_url() or chainstack_rpc_url() or triton_rpc_url()),
        "helius_key": bool(_env("HELIUS_API_KEY") or _env("HELIUS_RPC_URL")),
        "quicknode_url": bool(quicknode_rpc_url()),
        "alchemy_key": bool(_env("ALCHEMY_API_KEY") or _env("ALCHEMY_RPC_URL")),
        "chainstack_url": bool(chainstack_rpc_url()),
        "triton_url": bool(triton_rpc_url()),
        "fomoapi": bool(_env("FOMOAPI_KEY", "FOMO_API_KEY")),
        "goplus": bool(_env("GOPLUS_API_KEY")),
        "jupiter_key": bool(_env("JUPITER_API_KEY")),
    }
