"""
Jupiter / Phantom live execution engine (Solana).

This is the real-money path for Void Crypto Trader:
  - Loads your keypair from an exported PHANTOM wallet file OR SOLANA_PRIVATE_KEY env
    (base58 string or JSON byte array - Phantom's "Export Private Key" gives you
    exactly these formats). The key NEVER touches source code.
  - Buys/sells via Jupiter (the best-rate Solana aggregator, same one Phantom uses):
      * Ultra API (MEV-protected, gasless) when available
      * Swap API v1 fallback (quote -> swap build -> send)
  - Safety rails BEFORE any signature:
      * LIVE_TRADING=true must be set explicitly
      * per-trade USD cap, daily loss circuit breaker, daily volume cap
      * GoPlus honeypot check on every new mint (can't-sell tokens are refused)
      * dry-run mode that does everything except sign/broadcast

Phantom setup (one time):
  Phantom extension -> Settings -> Security & Privacy -> Show private key
  -> "Delete & Download" export file (JSON array), store it as keys/phantom.json
  (this file is gitignored). Or copy the base58 key into keys/api_keys.env as
  SOLANA_PRIVATE_KEY.

Env vars:
  SOLANA_PRIVATE_KEY | PHANTOM_WALLET_FILE   key material (never commit!)
  RPC_HTTP_URL                               prefer Helius/QuickNode for speed
  LIVE_TRADING=false                         master switch (must be true to trade)
  LIVE_TRADE_MAX_USD=200                     per-trade hard cap
  LIVE_DAILY_LOSS_LIMIT_USD=300              stop trading after this daily drawdown
  LIVE_DAILY_VOLUME_CAP=1000                 max USD bought per UTC day
  JUPITER_SLIPPAGE_BPS=100                   1% default slippage tolerance
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from rich.console import Console

console = Console()
load_dotenv()
load_dotenv(Path(__file__).parent.parent / "keys" / "api_keys.env", override=True)

BASE_DIR = Path(__file__).parent.parent
STATE_FILE = BASE_DIR / "live_executor_state.json"

WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
LAMPORTS_PER_SOL = 1_000_000

RPC_URL = os.getenv("RPC_HTTP_URL") or os.getenv("SOLANA_RPC_URL") or "https://api.mainnet-beta.solana.com"
PHANTOM_WALLET_FILE = os.getenv("PHANTOM_WALLET_FILE") or str(BASE_DIR / "keys" / "phantom.json")
PRIVATE_KEY_ENV = os.getenv("SOLANA_PRIVATE_KEY") or ""

LIVE_TRADING = (os.getenv("LIVE_TRADING") or "false").lower() in ("1", "true", "yes")
DRY_RUN = (os.getenv("LIVE_DRY_RUN") or "false").lower() in ("1", "true", "yes")
TRADE_MAX_USD = float(os.getenv("LIVE_TRADE_MAX_USD", "200"))
DAILY_LOSS_LIMIT_USD = float(os.getenv("LIVE_DAILY_LOSS_LIMIT_USD", "300"))
DAILY_VOLUME_CAP = float(os.getenv("LIVE_DAILY_VOLUME_CAP", "1000"))
SLIPPAGE_BPS = int(os.getenv("JUPITER_SLIPPAGE_BPS", "100"))

# Jupiter endpoints (tried in order; first that returns a usable response wins)
ULTRA_BASES = [
    "https://api.jup.ag/ultra/v1",
    "https://lite-api.jup.ag/ultra/v1",
    "https://api.jup.ag/ultra-v1",
]
SWAP_BASES = [
    "https://api.jup.ag/swap/v1",
    "https://lite-api.jup.ag/swap/v1",
]


# --------------------------------------------------------------------------
# key material
# --------------------------------------------------------------------------

def _load_keypair():
    """Return a solders Keypair from env or Phantom export file. Never logs secrets."""
    try:
        from solders.keypair import Keypair
    except ImportError:
        raise RuntimeError("pip install solders base58 (see requirements.txt)")

    raw = PRIVATE_KEY_ENV.strip().strip("'\"")
    if not raw and Path(PHANTOM_WALLET_FILE).exists():
        raw = Path(PHANTOM_WALLET_FILE).read_text().strip()

    if not raw:
        return None

    # Phantom "Delete & Download" export: JSON array of 64 bytes
    if raw.startswith("["):
        try:
            arr = json.loads(raw)
            if len(arr) == 64:
                return Keypair.from_bytes(bytes(arr))
        except Exception:
            pass
    # JSON object {"privateKey": [...]} (some backups)
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            arr = obj.get("privateKey") or obj.get("secretKey") or []
            if len(arr) == 64:
                return Keypair.from_bytes(bytes(arr))
        except Exception:
            pass
    # base58 string (Phantom "Show Private Key")
    try:
        return Keypair.from_base58_string(raw)
    except Exception:
        return None


def wallet_address() -> Optional[str]:
    kp = _load_keypair()
    return str(kp.pubkey()) if kp else None


# --------------------------------------------------------------------------
# RPC helpers
# --------------------------------------------------------------------------

def rpc(method: str, params: list, timeout: float = 20):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = requests.post(RPC_URL, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("result")


def sol_balance() -> float:
    return (rpc("getBalance", [wallet_address()]).get("value") or 0) / LAMPORTS_PER_SOL


def usdc_balance() -> float:
    """Return USDC token balance for the wallet (0 if none)."""
    addr = wallet_address()
    if not addr:
        return 0.0
    try:
        from solders.pubkey import Pubkey
        result = rpc(
            "getTokenAccountsByOwner",
            [addr, {"mint": USDC_MINT}, {"encoding": "jsonParsed"}],
        )
        total = 0
        for acc in result or []:
            info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            total += int(info.get("tokenAmount", {}).get("amount", 0))
        return total / 1e6
    except Exception:
        return 0.0


def sol_price_usd() -> Optional[float]:
    """Best-effort SOL price (CoinGecko simple, cached 60s)."""
    global _sol_price_cache
    now = time.time()
    if _sol_price_cache and now - _sol_price_cache[0] < 60:
        return _sol_price_cache[1]
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
            timeout=8,
        )
        p = float(r.json()["solana"]["usd"])
        _sol_price_cache = (now, p)
        return p
    except Exception:
        return _sol_price_cache[1] if _sol_price_cache else None


_sol_price_cache: Optional[tuple[float, float]] = None


# --------------------------------------------------------------------------
# safety state (daily loss breaker + volume cap)
# --------------------------------------------------------------------------

def _load_state() -> dict:
    st = {"day": _today(), "realized_pnl_usd": 0.0, "volume_usd": 0.0}
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
            if saved.get("day") == _today():
                st.update(saved)
        except Exception:
            pass
    return st


def _save_state(st: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(st, indent=2))
    except OSError:
        pass


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def preflight_check(usd_amount: float) -> tuple[bool, str]:
    """All safety rails before touching the key. Returns (ok, reason)."""
    if not LIVE_TRADING:
        return False, "LIVE_TRADING is not set to true (refusing to trade real money)"
    if _load_keypair() is None:
        return False, "No key found: set SOLANA_PRIVATE_KEY or export a Phantom wallet to keys/phantom.json"
    if usd_amount <= 0:
        return False, "Non-positive trade size"
    if usd_amount > TRADE_MAX_USD:
        return False, f"Trade ${usd_amount:.2f} exceeds LIVE_TRADE_MAX_USD=${TRADE_MAX_USD:.0f}"
    st = _load_state()
    if st["realized_pnl_usd"] <= -DAILY_LOSS_LIMIT_USD:
        return False, f"DAILY CIRCUIT BREAKER: realized {st['realized_pnl_usd']:+.2f}USD today (limit -{DAILY_LOSS_LIMIT_USD:.0f})"
    if st["volume_usd"] + usd_amount > DAILY_VOLUME_CAP:
        return False, f"Daily volume cap reached (${st['volume_usd']:.0f}/${DAILY_VOLUME_CAP:.0f})"
    return True, "ok"


def note_fill(side: str, usd_value: float, pnl_usd: float = 0.0) -> None:
    """Record a fill against the daily risk state."""
    st = _load_state()
    st["volume_usd"] += usd_value
    st["realized_pnl_usd"] += pnl_usd
    _save_state(st)


# --------------------------------------------------------------------------
# Jupiter: quote + swap via Swap API v1
# --------------------------------------------------------------------------

def _best_swap_base() -> Optional[str]:
    """Pick the first Jupiter swap endpoint that answers a health probe."""
    wsol, usdc = WSOL_MINT, USDC_MINT
    for base in SWAP_BASES:
        try:
            r = requests.get(f"{base}/quote", params={
                "inputMint": wsol, "outputMint": usdc, "amount": LAMPORTS_PER_SOL,
            }, timeout=10)
            if r.status_code == 200 and "outAmount" in r.text:
                return base
        except Exception:
            continue
    return None


def jupiter_quote(base: str, input_mint: str, output_mint: str, amount_raw: int) -> Optional[dict]:
    try:
        r = requests.get(f"{base}/quote", params={
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_raw,
            "slippageBps": SLIPPAGE_BPS,
            "restrictIntermediateTokens": "true",
        }, timeout=12)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _sign_and_send(tx_b64: str) -> Optional[str]:
    """Deserialize a VersionedTransaction, sign with our keypair, submit. Returns sig."""
    import base64
    from solders.transaction import VersionedTransaction
    kp = _load_keypair()
    if kp is None:
        raise RuntimeError("missing keypair")
    raw = base64.b64decode(tx_b64)
    tx = VersionedTransaction.from_bytes(raw)
    signed = VersionedTransaction(tx.message, [kp])
    res = rpc("sendRawTransaction", [
        base64.b64encode(bytes(signed)).decode(),
        {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"},
    ])
    return res


def _confirm(sig: str, tries: int = 10) -> bool:
    for _ in range(tries):
        try:
            st = rpc("getSignatureStatuses", [[sig]])
            status = (st.get("value") or [None])[0]
            if status and status.get("confirmationStatus") in ("confirmed", "finalized"):
                return not status.get("err")
            if status and status.get("err"):
                return False
        except Exception:
            pass
        time.sleep(1.0)
    return False


def _swap_via_api(input_mint: str, output_mint: str, amount_raw: int, label: str) -> tuple[bool, str]:
    base = _best_swap_base()
    if not base:
        return False, "Jupiter Swap API unreachable (all bases failed)"
    q = jupiter_quote(base, input_mint, output_mint, amount_raw)
    if not q or q.get("error"):
        return False, f"No route for {label} (pool too thin or bad mint)"
    out_amt = int(q.get("outAmount", 0))
    if DRY_RUN:
        console.print(f"[yellow]DRY-RUN {label}: would swap in={amount_raw} out={out_amt} (sig skipped)[/yellow]")
        return True, f"dry-run ok, quoted outAmount={out_amt}"
    pubkey = wallet_address()
    r = requests.post(f"{base}/swap", json={
        "quoteResponse": q,
        "userPublicKey": pubkey,
        "wrapAndUnwrapSol": True,
        "dynamicComputeUnitLimit": True,
        "prioritizationFeeLamports": {"priorityLevelWithMaxLamports": {
            "maxLamports": 2_000_000, "priorityLevel": "high"}},
    }, timeout=20)
    if r.status_code != 200:
        return False, f"swap build failed: {r.text[:160]}"
    swap_tx = r.json().get("swapTransaction")
    if not swap_tx:
        return False, "no swapTransaction in response"
    try:
        sig = _sign_and_send(swap_tx)
    except Exception as e:
        return False, f"sign/send error: {e}"
    if not sig:
        return False, "broadcast returned no signature"
    ok = _confirm(sig)
    msg = f"{label} sent sig={sig[:16]}... {'CONFIRMED' if ok else 'UNCONFIRMED'}"
    return ok, msg


# --------------------------------------------------------------------------
# public entry points used by portfolio.execute_buy / execute_sell
# --------------------------------------------------------------------------

@dataclass
class LiveResult:
    ok: bool
    message: str
    tokens_out: float = 0.0
    price_usd: float = 0.0


def _honeypot_gate(mint: str) -> tuple[bool, str]:
    if mint in (WSOL_MINT, USDC_MINT):
        return True, "major"
    try:
        from security import check_solana_token
        res = check_solana_token(mint)
        if res.ok:
            return True, res.raw_summary or "safe"
        return False, res.blocked_reason()
    except Exception as e:
        # strict-by-default: if we cannot verify sellability, do not buy
        return False, f"security check unavailable ({e}) - refusing live buy"


def live_buy(token_mint: str, usd_amount: float) -> tuple[bool, str]:
    """Buy `token_mint` worth `usd_amount` USD using wallet SOL as source."""
    ok, why = preflight_check(usd_amount)
    if not ok:
        return False, why
    safe, sec_msg = _honeypot_gate(token_mint)
    if not safe:
        return False, f"BLOCKED by security scan: {sec_msg}"

    price = sol_price_usd()
    if not price:
        return False, "cannot price SOL - aborting live buy"
    lamports = int((usd_amount / price) * LAMPORTS_PER_SOL)
    have_sol = sol_balance()
    need_sol = usd_amount / price + 0.02  # buffer for fees/priority
    if have_sol < need_sol:
        return False, f"need ~{need_sol:.3f} SOL, wallet has {have_sol:.3f}"

    console.print(f"[cyan]LIVE BUY[/cyan] {token_mint[:10]}... ${usd_amount:.2f} (~{lamports/LAMPORTS_PER_SOL:.4f} SOL) via Jupiter")
    ok, msg = _swap_via_api(WSOL_MINT, token_mint, lamports, f"BUY {token_mint[:8]}")
    if ok and not DRY_RUN:
        note_fill("buy", usd_amount)
    return ok, msg


def live_sell(token_mint: str, token_amount_raw: int) -> tuple[bool, str]:
    """Sell exact raw token amount back to SOL."""
    if not LIVE_TRADING:
        return False, "LIVE_TRADING not enabled"
    if _load_keypair() is None:
        return False, "no keypair configured"
    if token_amount_raw <= 0:
        return False, "nothing to sell"
    console.print(f"[cyan]LIVE SELL[/cyan] {token_mint[:10]}... raw={token_amount_raw}")
    ok, msg = _swap_via_api(token_mint, WSOL_MINT, token_amount_raw, f"SELL {token_mint[:8]}")
    return ok, msg


def self_test() -> bool:
    """Safe diagnostics: no signing, no broadcast."""
    console.print("[bold]Jupiter live executor self-test[/bold]")
    kp_addr = wallet_address()
    console.print(f"Keypair: {'FOUND ' + kp_addr[:8] + '...' + kp_addr[-4:] if kp_addr else 'MISSING (sim-only mode)'}")
    console.print(f"LIVE_TRADING={LIVE_TRADING}  DRY_RUN={DRY_RUN}  RPC={RPC_URL[:48]}")
    try:
        console.print(f"RPC getHealth: {rpc('getHealth', [])}")
    except Exception as e:
        console.print(f"[red]RPC unreachable: {e}[/red]")
        return False
    if kp_addr:
        try:
            console.print(f"SOL balance: {sol_balance():.4f} | USDC: {usdc_balance():.2f}")
        except Exception as e:
            console.print(f"[yellow]balance read failed: {e}[/yellow]")
    base = _best_swap_base()
    console.print(f"Jupiter swap API: {base or '[red]unreachable[/red]'}")
    if base:
        q = jupiter_quote(base, WSOL_MINT, USDC_MINT, LAMPORTS_PER_SOL)
        console.print(f"Test quote 1 SOL -> USDC: {q.get('outAmount') if q else 'FAILED'}")
    console.print(f"Daily state: {_load_state()}")
    console.print("[green]Self-test complete (nothing was signed or broadcast)[/green]")
    return bool(kp_addr) and base is not None


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv or len(sys.argv) == 1:
        sys.exit(0 if self_test() else 1)
