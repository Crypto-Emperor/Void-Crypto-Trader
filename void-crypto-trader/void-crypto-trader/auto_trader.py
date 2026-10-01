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

    # ---- freqtrade-style risk overlay (circuit breakers + protections) ----
    from risk import BreakerState, update_breakers, can_open_new, risk_config
    import protections as prot_mod

    syms = list(set(settings.symbol_list + list(pf.positions.keys())))
    prices = get_prices(syms, settings.price_source)

    brk = _breaker_state(acc)
    equity_now = pf.total_equity(prices)
    brk = update_breakers(brk, equity_now, risk_config)
    _save_breaker_state(acc, brk)
    ok_new, why_new = can_open_new(brk)

    records = prot_mod.trades_from_portfolio(pf)
    prots = prot_mod.get_protections()
    g_locked, g_why = prots.global_locked()

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

    # ---- enforce global gates on BUYS only (exits must always run) --------
    gated: list = []
    for sig in signals:
        if sig.action == "buy":
            if not ok_new:
                log(f"[{acc.name}] BUY BLOCKED {sig.symbol}: {why_new}")
                continue
            if g_locked:
                log(f"[{acc.name}] BUY BLOCKED {sig.symbol}: protection {g_why}")
                continue
            bok, bwhy = prots.can_enter(sig.symbol, records)
            if not bok:
                log(f"[{acc.name}] BUY BLOCKED {sig.symbol}: protection {bwhy}")
                continue
        gated.append(sig)
    signals = gated

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
            # adopt planned structural stops/targets from the signal engine
            if ok:
                try:
                    from strategies.confluence import planned_stops
                    sp_tp = planned_stops(acc.id, sig.symbol)
                    if sp_tp and sig.symbol in pf.positions:
                        pos = pf.positions[sig.symbol]
                        stop_price, take_profit = sp_tp
                        if pos.stop_price is None or stop_price < pos.avg_entry:
                            pos.stop_price = stop_price
                        if pos.take_profit is None:
                            pos.take_profit = take_profit
                        if pos.highest_seen is None:
                            pos.highest_seen = pos.avg_entry
                        pf.save()
                except Exception as e:
                    log(f"[{acc.name}] stop-plan skip: {e}")
            save_for_account(pf, acc)
            tag = "TRADE BUY " if ok else "FAIL BUY "
            log(f"[{acc.name}] {tag}{msg}" + (f" | {sig.reason}" if sig.reason else ""))
        elif sig.action == "sell" and sig.amount is not None:
            had_position = sig.symbol in pf.positions
            ok, msg = execute_sell(
                pf, sig.symbol, sig.amount,
                source=settings.price_source, note=sig.reason,
            )
            if ok and had_position:
                # fully closed? stamp cooldown like freqtrade CooldownPeriod
                try:
                    prots = __import__("protections").get_protections()
                    if sig.symbol not in pf.positions:
                        prots.note_exit(sig.symbol)
                except Exception:
                    pass
            save_for_account(pf, acc)
            tag = "TRADE SELL " if ok else "FAIL SELL "
            log(f"[{acc.name}] {tag}{msg}" + (f" | {sig.reason}" if sig.reason else ""))


# ---------------- breaker-state persistence (per account) ----------------

def _breaker_path(acc):
    from config import BASE_DIR
    from pathlib import Path
    safe = "".join(c if c.isalnum() else "_" for c in str(acc.id))
    return Path(BASE_DIR / "data" / f"breakers_{safe}.json")


def _breaker_state(acc) -> "BreakerState":
    from risk import BreakerState
    import json
    p = _breaker_path(acc)
    try:
        if p.exists():
            return BreakerState(**json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        pass
    return BreakerState()


def _save_breaker_state(acc, state) -> None:
    import json
    from dataclasses import asdict
    p = _breaker_path(acc)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(state)), encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    run_loop()
