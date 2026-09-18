"""
Background auto-trader - multi-account (up to 5) + leaderboard-aware strategies.
"""

from __future__ import annotations
import os
import sys
import time
import signal
from datetime import datetime, timezone

from rich.console import Console
from config import settings, PID_FILE, STOP_FLAG, LOG_FILE
from portfolio import execute_buy, execute_sell, load_for_account, save_for_account
from prices import get_prices
from strategies import get_strategy
from accounts import enabled_accounts

console = Console()


def _ascii(s: str) -> str:
    """Windows cp1252-safe text (no arrows / fancy dashes)."""
    table = {
        "\u2192": "->",
        "\u2190": "<-",
        "\u2014": "-",
        "\u2013": "-",
        "\u2248": "~",
        "\u2264": "<=",
        "\u2265": ">=",
        "\u2026": "...",
        "\u03c3": "sigma",
        "\u00a0": " ",
    }
    out = str(s)
    for k, v in table.items():
        out = out.replace(k, v)
    return out.encode("ascii", errors="replace").decode("ascii")


def log(msg: str) -> None:
    line = _ascii(f"{datetime.now(timezone.utc).isoformat()}  {msg}")
    try:
        print(line, flush=True)
    except Exception:
        try:
            sys.stdout.buffer.write((line + chr(10)).encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        except Exception:
            pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + chr(10))
    except Exception:
        pass


def write_pid() -> None:
    PID_FILE.write_text(str(os.getpid()))


def clear_pid() -> None:
    if PID_FILE.exists():
        PID_FILE.unlink(missing_ok=True)
    if STOP_FLAG.exists():
        STOP_FLAG.unlink(missing_ok=True)


def should_stop() -> bool:
    return STOP_FLAG.exists()


def run_loop() -> None:
    write_pid()
    if STOP_FLAG.exists():
        STOP_FLAG.unlink(missing_ok=True)

    accounts = enabled_accounts()
    log(
        f"Auto-trader START  accounts={len(accounts)}  "
        f"global_strategy={settings.strategy}  interval={settings.check_interval_sec}s"
    )
    for a in accounts:
        log(f"  account id={a.id} name={a.name} mode={a.mode} capital=${a.starting_balance:,.0f}")

    def _sig(*_):
        log("Signal received – stopping")
        STOP_FLAG.write_text("1")

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    try:
        while not should_stop():
            accounts = enabled_accounts()
            if not accounts:
                log("No enabled accounts - idle")
            for acc in accounts:
                if should_stop():
                    break
                try:
                    _tick_account(acc)
                except Exception as e:
                    log(f"[{acc.name}] error: {e}")

            slept = 0.0
            interval = max(0.2, float(settings.check_interval_sec))
            while slept < interval and not should_stop():
                step = min(1.0, interval - slept)
                time.sleep(step)
                slept += step
    finally:
        clear_pid()
        log("Auto-trader STOPPED")


def _tick_account(acc) -> None:
    strat_name = acc.strategy or settings.strategy
    strategy = get_strategy(strat_name, account_id=acc.id)
    pf = load_for_account(acc)

    # prices: global symbols + any open positions
    syms = list(set(settings.symbol_list + list(pf.positions.keys())))
    prices = get_prices(syms, settings.price_source)

    signals = strategy.generate(pf, prices)
    # Always apply defensive sells (drop / predicted drop)
    try:
        from sell_rules import defensive_sell_signals
        def_sells = defensive_sell_signals(pf, prices)
        if def_sells:
            # sells first
            signals = def_sells + [s for s in signals if not (s.action == "sell" and s.symbol in {d.symbol for d in def_sells})]
            log(f"[{acc.name}] defensive sells: {len(def_sells)}")
    except Exception as e:
        log(f"[{acc.name}] sell_rules skip: {e}")

    # brief next-move hint (non-blocking)
    try:
        from predict import forecast_portfolio
        fc = forecast_portfolio(pf)
        if fc.token_predictions:
            top = fc.token_predictions[0]
            log(
                f"[{acc.name}] forecast: equity->${fc.expected_equity:,.0f} "
                f"({fc.expected_pnl:+,.0f}) | strongest {top.symbol} {top.bias} {top.confidence}%"
            )
    except Exception:
        pass
    if not signals:
        eq = pf.total_equity(prices)
        log(f"[{acc.name}] no signals | equity~${eq:,.2f} | strategy={strat_name}")
        return

    for sig in signals:
        log(f"[{acc.name}] SIGNAL {sig.action.upper()} {sig.symbol} "
            f"{'usd='+str(sig.usd_amount) if sig.usd_amount else 'amt='+str(sig.amount)} "
            f"| {sig.reason or ''}")
        if sig.action == "buy" and sig.usd_amount:
            ok, msg = execute_buy(
                pf, sig.symbol, sig.usd_amount,
                source=settings.price_source, note=sig.reason,
            )
            save_for_account(pf, acc)
            tag = "TRADE BUY " if ok else "FAIL BUY "
            log(f"[{acc.name}] {tag}{msg}" + (f" | {sig.reason}" if sig.reason else ""))
        elif sig.action == "sell" and sig.amount is not None:
            ok, msg = execute_sell(
                pf, sig.symbol, sig.amount,
                source=settings.price_source, note=sig.reason,
            )
            save_for_account(pf, acc)
            tag = "TRADE SELL " if ok else "FAIL SELL "
            log(f"[{acc.name}] {tag}{msg}" + (f" | {sig.reason}" if sig.reason else ""))


if __name__ == "__main__":
    run_loop()
