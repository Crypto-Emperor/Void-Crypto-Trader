"""
Live FOMO.family trade executor via browser automation (Playwright).

IMPORTANT
---------
FOMO does not publish a public trading API.
This module drives the web UI at https://fomo.family.

Status: SCAFFOLD / TEMPLATE
- Login and navigation are implemented as a starting point.
- Exact selectors for buy/sell buttons, token search, confirm dialogs
  change over time and MUST be updated by inspecting the live site
  (DevTools → copy selector) after you log in once.

How to finish wiring:
1. Install browsers:  playwright install chromium
2. Put FOMO_EMAIL + FOMO_PASSWORD (or FOMO_ACCESS_TOKEN if you reverse-engineer auth) in .env
3. Set MODE=live
4. Run once with headed browser (HEADLESS=false) and fix any broken selectors
5. Only then leave it unattended

This is experimental. Real money can be lost. Test with tiny sizes first.
"""

from __future__ import annotations
import os
from pathlib import Path
from rich.console import Console

console = Console()
BASE = "https://fomo.family"
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"
STATE_DIR = Path(__file__).parent.parent / ".browser_state"
STATE_DIR.mkdir(exist_ok=True)


def _get_browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed. Run: pip install playwright && playwright install chromium"
        )
    return sync_playwright()


def _login(page) -> bool:
    """Attempt login. Adjust selectors after inspecting the real site."""
    email = os.getenv("FOMO_EMAIL")
    password = os.getenv("FOMO_PASSWORD")
    if not email or not password:
        console.print("[red]FOMO_EMAIL and FOMO_PASSWORD required for live mode[/red]")
        return False

    page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=60000)
    # These selectors are placeholders – update them from DevTools
    try:
        page.fill('input[type="email"], input[name="email"]', email, timeout=10000)
        page.fill('input[type="password"], input[name="password"]', password, timeout=5000)
        page.click('button[type="submit"], button:has-text("Sign in"), button:has-text("Log in")')
        page.wait_for_timeout(3000)
        return True
    except Exception as e:
        console.print(f"[yellow]Login selectors may need updating: {e}[/yellow]")
        return False


def live_buy(symbol: str, usd_amount: float) -> tuple[bool, str]:
    """
    Open FOMO, search token, enter USD amount, confirm buy.
    Returns (success, message).
    """
    console.print(f"[cyan]LIVE BUY {symbol.upper()} ${usd_amount:.2f} via FOMO UI…[/cyan]")
    with _get_browser() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            storage_state=str(STATE_DIR / "storage.json")
            if (STATE_DIR / "storage.json").exists()
            else None
        )
        page = context.new_page()
        try:
            if not (STATE_DIR / "storage.json").exists():
                if not _login(page):
                    return False, "login failed – update selectors or credentials"
                context.storage_state(path=str(STATE_DIR / "storage.json"))

            page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
            # TODO: real UI flow
            # 1. Click search / trade
            # 2. Type symbol or paste mint
            # 3. Enter USD amount
            # 4. Click Buy / Confirm
            # page.fill(...); page.click(...)
            page.wait_for_timeout(2000)

            # Until selectors are fixed we refuse to pretend the trade succeeded
            return False, (
                "LIVE executor is a scaffold. Open fomo.family in a browser, "
                "inspect the buy flow, and fill in the real selectors in "
                "executor/live_fomo.py before using real money."
            )
        except Exception as e:
            return False, str(e)
        finally:
            browser.close()


def live_sell(symbol: str, token_amount: float) -> tuple[bool, str]:
    console.print(f"[cyan]LIVE SELL {token_amount} {symbol.upper()} via FOMO UI…[/cyan]")
    # Same structure as live_buy – implement after inspecting UI
    return False, (
        "LIVE sell is a scaffold. Wire selectors in executor/live_fomo.py first."
    )
