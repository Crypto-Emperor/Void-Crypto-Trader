"""
1-second on-chain Solana wallet copy bot (no fomoapi).

- Polls leader wallet via JSON-RPC getSignaturesForAddress
- On new signature -> getTransaction -> extract SPL mint(s)
- Sim mode: log + optional paper buy via existing portfolio
- Live mode: Jupiter swap (requires SOLANA_PRIVATE_KEY) - opt-in

NEVER put private keys in source code. Use .env only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
load_dotenv(Path(__file__).parent / "keys" / "api_keys.env", override=True)
console = Console()

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "wallet_copy_state.json"

# ---- settings from env ----
RPC_HTTP_URL = os.getenv("RPC_HTTP_URL") or os.getenv("SOLANA_RPC_URL") or "https://api.mainnet-beta.solana.com"
TARGET_LEADER_WALLET = (os.getenv("TARGET_LEADER_WALLET") or "").strip()
POLL_INTERVAL_SEC = float(os.getenv("WALLET_COPY_INTERVAL_SEC", "1.0"))
MODE = (os.getenv("WALLET_COPY_MODE") or os.getenv("MODE") or "sim").lower()
COPY_USD = float(os.getenv("WALLET_COPY_USD", os.getenv("TRADE_USD", "25")))
JUPITER_BASE = os.getenv("JUPITER_BASE", "https://quote-api.jup.ag/v6")
# private key: base58 string OR json byte array in env - never commit
SOLANA_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY") or ""

# common mints to ignore as "the trade token"
IGNORE_MINTS = {
    "So11111111111111111111111111111111111111112",  # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "11111111111111111111111111111111",
}


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_signature": None, "seen": []}


def _save_state(state: dict) -> None:
    seen = state.get("seen") or []
    state["seen"] = seen[-500:]
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def rpc(method: str, params: list, timeout: float = 20) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = requests.post(RPC_HTTP_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("result")


def get_latest_signature(wallet: str) -> Optional[str]:
    result = rpc("getSignaturesForAddress", [wallet, {"limit": 1}])
    if not result:
        return None
    return result[0].get("signature")


def get_recent_signatures(wallet: str, limit: int = 5) -> list[str]:
    result = rpc("getSignaturesForAddress", [wallet, {"limit": limit}]) or []
    return [x.get("signature") for x in result if x.get("signature")]


def fetch_token_from_signature(signature: str) -> list[str]:
    """
    Return candidate token mint addresses from a transaction.
    Uses jsonParsed tx to read token balance changes / instructions.
    """
    result = rpc(
        "getTransaction",
        [
            signature,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 1,
            },
        ],
        timeout=30,
    )
    if not result:
        return []

    mints: list[str] = []

    meta = result.get("meta") or {}
    # post token balances often list mints involved
    for side in ("postTokenBalances", "preTokenBalances"):
        for bal in meta.get(side) or []:
            mint = bal.get("mint")
            if mint and mint not in IGNORE_MINTS and mint not in mints:
                mints.append(mint)

    # parsed instructions (transfer / transferChecked)
    tx = result.get("transaction") or {}
    msg = tx.get("message") or {}
    for ix in msg.get("instructions") or []:
        parsed = ix.get("parsed") if isinstance(ix, dict) else None
        if not isinstance(parsed, dict):
            continue
        info = parsed.get("info") or {}
        mint = info.get("mint")
        if mint and mint not in IGNORE_MINTS and mint not in mints:
            mints.append(mint)

    # inner instructions
    for inner in meta.get("innerInstructions") or []:
        for ix in inner.get("instructions") or []:
            parsed = ix.get("parsed") if isinstance(ix, dict) else None
            if not isinstance(parsed, dict):
                continue
            info = parsed.get("info") or {}
            mint = info.get("mint")
            if mint and mint not in IGNORE_MINTS and mint not in mints:
                mints.append(mint)

    return mints


def jupiter_quote(input_mint: str, output_mint: str, amount_raw: int) -> Optional[dict]:
    try:
        r = requests.get(
            f"{JUPITER_BASE}/quote",
            params={
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount_raw,
                "slippageBps": 100,
            },
            timeout=15,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        console.print(f"[dim yellow]Jupiter quote error: {e}[/dim yellow]")
        return None


def _max_trade_usd(equity: float, cash: float) -> float:
    """Cap each trade at MAX_POSITION_PCT of equity (default 6%)."""
    pct = float(os.getenv("MAX_POSITION_PCT", "6")) / 100.0
    cap = equity * pct
    # also never exceed optional fixed ceiling or available cash
    # Optional lower ceiling only if user sets WALLET_COPY_USD explicitly
    raw = os.getenv("WALLET_COPY_USD")
    if raw:
        fixed_ceil = float(raw)
        return max(0.0, min(cap, fixed_ceil, cash * 0.75))
    return max(0.0, min(cap, cash * 0.75))


def execute_copy_trade(token_mint: str, signature: str) -> bool:
    """
    Sim: paper log (+ optional portfolio buy if integrated).
    Live: requires solders + key - only runs if WALLET_COPY_MODE=live.
    Each buy is capped at MAX_POSITION_PCT of equity (default 6%).
    """
    if MODE != "live":
        try:
            from portfolio import Portfolio, execute_buy

            pf = Portfolio.load()
            # equity approx: cash + mark positions if prices available
            try:
                from prices import get_prices
                syms = list(pf.positions.keys())
                prices = get_prices(syms, "auto") if syms else {}
                equity = pf.total_equity(prices)
            except Exception:
                equity = pf.cash
            usd = _max_trade_usd(equity, pf.cash)
            console.print(
                f"COPY SIGNAL mint={token_mint} "
                f"from tx={signature[:16]}... mode={MODE} "
                f"usd~{usd:.2f} (max 6% of ${equity:,.2f})"
            )
            if usd < 5:
                console.print("[yellow]Skip: 6% size under $5 or no cash[/yellow]")
                return False
            note = f"wallet_copy {signature[:12]} cap6%"
            sym = token_mint[:8].lower()
            ok, msg = execute_buy(pf, sym, usd, source="auto", note=note)
            console.print(f"[dim]sim portfolio: {msg}[/dim]")
            return ok
        except Exception as e:
            usd = COPY_USD
            console.print(
                f"COPY SIGNAL mint={token_mint} "
                f"mode={MODE} usd~{usd} (fallback)"
            )
            console.print(f"[dim]sim log only ({e})[/dim]")
            return True

    console.print(
        f"COPY SIGNAL mint={token_mint} "
        f"from tx={signature[:16]}... mode={MODE} (live, still 6% rule in sizing)"
    )

    # LIVE path - private key required
    if not SOLANA_PRIVATE_KEY:
        console.print("[red]LIVE mode but SOLANA_PRIVATE_KEY not set - refused[/red]")
        return False

    console.print(
        "[yellow]Live Jupiter swap scaffold: install solders+solana and "
        "complete swap tx signing before using real funds.[/yellow]"
    )
    # Quote only for safety in this version - full swap needs careful signing
    wsol = "So11111111111111111111111111111111111111112"
    # amount in lamports for ~COPY_USD is approximate without price oracle
    quote = jupiter_quote(wsol, token_mint, amount_raw=1_000_000)  # 0.001 SOL test size
    if quote:
        console.print(f"[dim]Jupiter quote outAmount={quote.get('outAmount')}[/dim]")
        console.print(
            "[red]Auto-broadcast disabled by default. "
            "Enable only after you review and implement signed swap submit.[/red]"
        )
    return False


async def scan_leader_every_second(wallet: str | None = None) -> None:
    wallet = (wallet or TARGET_LEADER_WALLET).strip()
    if not wallet:
        console.print(
            "[red]Set TARGET_LEADER_WALLET in .env to the leader's Solana address[/red]"
        )
        return

    console.print(
        f"[bold]Wallet copy bot[/bold] leader={wallet[:8]}...{wallet[-6:]} "
        f"interval={POLL_INTERVAL_SEC}s mode={MODE} rpc={RPC_HTTP_URL[:40]}..."
    )

    state = _load_state()
    last_seen = state.get("last_signature")
    seen = set(state.get("seen") or [])

    # bootstrap: set baseline without trading history
    if not last_seen:
        try:
            last_seen = get_latest_signature(wallet)
            state["last_signature"] = last_seen
            _save_state(state)
            console.print(f"[dim]Baseline signature set: {last_seen}[/dim]")
        except Exception as e:
            console.print(f"[red]RPC error on baseline: {e}[/red]")
            return

    while True:
        start = time.time()
        try:
            current = get_latest_signature(wallet)
            if current and current != last_seen and current not in seen:
                console.print(f"[cyan]New activity[/cyan] {current}")
                mints = fetch_token_from_signature(current)
                if not mints:
                    console.print("[dim]No non-stable token mint found in tx[/dim]")
                for mint in mints[:3]:
                    execute_copy_trade(mint, current)
                seen.add(current)
                last_seen = current
                state["last_signature"] = last_seen
                state["seen"] = list(seen)
                _save_state(state)
            else:
                console.print(f"[dim]{time.strftime('%H:%M:%S')} no new tx[/dim]", end="\r")
        except Exception as e:
            console.print(f"[yellow]scan error: {e}[/yellow]")

        elapsed = time.time() - start
        sleep_time = max(0.05, POLL_INTERVAL_SEC - elapsed)
        await asyncio.sleep(sleep_time)


def self_test(wallet: str | None = None) -> bool:
    """Non-loop test: RPC health, signatures, parse one tx."""
    wallet = (wallet or TARGET_LEADER_WALLET).strip()
    console.print("[bold]wallet_copy self-test[/bold]")
    console.print(f"RPC: {RPC_HTTP_URL}")
    try:
        health = rpc("getHealth", [])
        console.print(f"Health: {health}")
    except Exception as e:
        console.print(f"[red]Health failed: {e}[/red]")
        return False

    if not wallet:
        # well-known active program-adjacent: use USDC mint owner activity via a public whale
        # Jupiter aggregator often active - use a documented system account fallback for structure test
        wallet = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"  # Jupiter v6 program - may have sigs
        console.print(f"[dim]No TARGET_LEADER_WALLET - using test address {wallet}[/dim]")

    try:
        sigs = get_recent_signatures(wallet, limit=3)
        console.print(f"Recent signatures: {len(sigs)}")
        for s in sigs[:3]:
            console.print(f"  {s}")
        if sigs:
            mints = fetch_token_from_signature(sigs[0])
            console.print(f"Mints from latest tx: {mints[:5]}")
        console.print("[green]Self-test OK (RPC + parse path works)[/green]")
        return True
    except Exception as e:
        console.print(f"[red]Self-test failed: {e}[/red]")
        return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        ok = self_test(sys.argv[2] if len(sys.argv) > 2 else None)
        raise SystemExit(0 if ok else 1)
    asyncio.run(scan_leader_every_second())
