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



def _journal(account: str, side: str, symbol: str, size, msg: str, latency_ms: float, reason: str) -> None:
    """Phase 1 paper trail: theoretical fills + latency vs signal time."""
    try:
        path = LOG_FILE.parent / "trade_journal.csv"
        is_new = not path.exists()
        with open(path, "a", encoding="utf-8") as f:
            if is_new:
                f.write("utc,account,side,symbol,size,latency_ms,msg,reason" + chr(10))
            ts = datetime.now(timezone.utc).isoformat()
            def safe(x):
                return str(x).replace(",", ";").replace(chr(10), " ").replace(chr(13), " ")
            f.write(
                f"{ts},{safe(account)},{side},{safe(symbol)},{safe(size)},"
                f"{latency_ms:.1f},{safe(msg)},{safe(reason)}" + chr(10)
            )
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


# Offline / fallback prices so the bot still trades when every free API is
# unreachable. Previously a price fetch failure left the tick with no symbols
# and the bot silently did nothing - which looked like "the bot doesn't work".
_DEMO_PRICES = {
    "btc": 95000.0, "eth": 3400.0, "sol": 150.0, "bonk": 0.00002,
    "wif": 1.5, "popcat": 0.48, "mew": 0.0048, "bome": 0.01,
    "slerf": 0.05, "wen": 0.00009, "trump": 9.2, "fartcoin": 1.1,
    "ai16z": 0.95, "goat": 0.45, "pnut": 0.28, "act": 0.02,
    "moodeng": 0.35, "jup": 0.85, "ray": 3.2, "ponke": 0.08,
    "myro": 0.12, "fwog": 0.03, "pyth": 0.38, "usdc": 1.0, "usdt": 1.0,
}


def _demo_prices(syms: list[str]) -> dict[str, float]:
    return {s: _DEMO_PRICES.get(s, 1.0) for s in syms}


def _sync_from_config_file() -> None:
    """Let config_exchange.json drive strategy/pairs/interval.

    The desktop app writes this file; without syncing, the engine kept using
    stale env defaults and ignored everything the user configured.
    """
    try:
        from config import BASE_DIR
        p = BASE_DIR / "config_exchange.json"
        if not p.exists():
            return
        import json as _json
        cfg = _json.loads(p.read_text(encoding="utf-8"))
        strat = cfg.get("strategy")
        if strat:
            settings.strategy = str(strat).lower()
        pairs = cfg.get("pairs")
        if pairs:
            settings.symbols = ",".join(str(x).lower() for x in pairs if str(x).strip())
        ci = cfg.get("check_interval_sec")
        if ci is not None:
            settings.check_interval_sec = max(1, int(ci))
        sb = cfg.get("starting_balance")
        if sb is not None:
            settings.starting_balance = float(sb)
        mp = cfg.get("max_position_pct")
        if mp is not None:
            settings.max_position_pct = float(mp)
    except Exception as e:
        log(f"config_exchange.json sync skipped: {e}")


def run_loop() -> None:
    write_pid()
    if STOP_FLAG.exists():
        STOP_FLAG.unlink(missing_ok=True)

    # Make sure the user's config file wins over stale env defaults BEFORE
    # anything else reads `settings`.
    _sync_from_config_file()

    accounts = enabled_accounts()
    log(
        f"Bot started | accounts={len(accounts)} | strategy={settings.strategy} | "
        f"pairs={settings.symbols} | check every {settings.check_interval_sec}s | "
        f"mode={settings.mode}"
    )
    try:
        from fomo_tokens import has_fomo_key
        if has_fomo_key():
            # Building the FOMO universe makes many network calls; do it after
            # the first market scan so the bot visibly starts trading at once.
            log("FOMO key detected | full token universe loads after first tick")
        else:
            log("FOMO mode OFF | set FOMOAPI_KEY in keys/api_keys.env for real FOMO memecoins")
            log("Get key: https://fomoapi.io/dashboard")
    except Exception as e:
        log(f"FOMO token check skipped: {e}")
    try:
        from pnl_rates import mark_session_start
        mark_session_start(settings.starting_balance, force=False)
    except Exception:
        pass
    for a in accounts:
        log(f"  Account '{a.name}' (id={a.id}) | mode={a.mode} | starting capital ${a.starting_balance:,.0f}")

    def _sig(*_):
        log("Signal received - stopping")
        try:
            STOP_FLAG.write_text("1")
        except Exception:
            pass

    # signal handlers only work on the main thread (GUI starts bot on a worker thread)
    try:
        import threading
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, _sig)
            signal.signal(signal.SIGTERM, _sig)
    except Exception:
        pass

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
        log("Bot stopped")


def _tick_account(acc) -> None:
    # Re-read config_exchange.json each tick so strategy/pair changes made in
    # the desktop app take effect without restarting the bot.
    _sync_from_config_file()

    strat_name = acc.strategy or settings.strategy
    strategy = get_strategy(strat_name, account_id=acc.id)
    pf = load_for_account(acc)

    # prices: global symbols + any open positions
    syms = list(set(settings.symbol_list + list(pf.positions.keys())))
    try:
        prices = get_prices(syms, settings.price_source)
    except Exception as e:
        log(f"[{acc.name}] price fetch issue ({e}) - using offline demo prices; sells still attempted")
        prices = {}
    missing = [s for s in syms if s not in prices]
    if missing:
        # Never leave a symbol without a price - that silently disables trades.
        for s in missing:
            prices[s] = _DEMO_PRICES.get(s, 1.0)
        if len(missing) == len(syms):
            log(f"[{acc.name}] No live prices (offline?) - running on demo prices so the bot keeps trading")
        else:
            log(f"[{acc.name}] Demo fallback prices for: {', '.join(sorted(missing))}")

    log(f"[{acc.name}] Scanning markets ({strat_name})...")
    try:
        signals = strategy.generate(pf, prices)
    except Exception as e:
        log(f"[{acc.name}] strategy error: {e}")
        signals = []
    # Always apply defensive sells (drop / predicted drop)
    try:
        from sell_rules import defensive_sell_signals
        def_sells = defensive_sell_signals(pf, prices)
        if def_sells:
            # sells first
            signals = def_sells + [s for s in signals if not (s.action == "sell" and s.symbol in {d.symbol for d in def_sells})]
            log(f"[{acc.name}] Protect: {len(def_sells)} sell(s) - price may be dropping")
    except Exception as e:
        log(f"[{acc.name}] sell_rules skip: {e}")

    # brief next-move hint (non-blocking)
    try:
        from predict import forecast_portfolio
        fc = forecast_portfolio(pf)
        if fc.token_predictions:
            top = fc.token_predictions[0]
            log(
                f"[{acc.name}] Outlook: strongest idea {top.symbol.upper()} "
                f"({top.bias}, conf {top.confidence}%) | "
                f"projected equity ~${fc.expected_equity:,.0f} ({fc.expected_pnl:+,.0f})"
            )
    except Exception:
        pass
    if not signals:
        eq = pf.total_equity(prices)
        log(f"[{acc.name}] Watching... no trade this tick | equity ${eq:,.2f} | strategy={strat_name}")
        try:
            from equity_history import record_equity
            record_equity(eq, pf.cash, eq - float(pf.starting_balance or 0))
        except Exception:
            pass
        return

    # Phase 1: multi-symbol in one tick + signal-to-fill latency logging
    tick_t0 = time.time()
    buys = [s for s in signals if s.action == "buy" and s.usd_amount]
    sells = [s for s in signals if s.action == "sell" and s.amount is not None]
    if buys or sells:
        log(
            f"[{acc.name}] This tick: {len(buys)} buy(s), {len(sells)} sell(s) "
            f"(multi-coin OK) | phase={os.getenv('TRADING_PHASE', 'sim')}"
        )

    filled_buys = filled_sells = 0
    for sig in signals:
        reason = (sig.reason or "strategy signal").strip()
        signal_t0 = time.time()
        if sig.action == "buy" and sig.usd_amount:
            usd = float(sig.usd_amount)
            # Phase 2 micro-live: scale absolute dollars without changing % rules
            if os.getenv("MICRO_LIVE", "").lower() in ("1", "true", "yes"):
                scale = float(os.getenv("MICRO_LIVE_SCALE", "1.0"))
                usd = max(1.0, usd * scale)
            log(
                f"[{acc.name}] PLAN BUY {sig.symbol.upper()} "
                f"for about ${usd:.2f} | why: {reason}"
            )
            ok, msg = execute_buy(
                pf, sig.symbol, usd,
                source=settings.price_source, note=sig.reason,
            )
            save_for_account(pf, acc)
            latency_ms = (time.time() - signal_t0) * 1000.0
            if ok:
                filled_buys += 1
                log(f"[{acc.name}] FILLED BUY | {msg} | latency {latency_ms:.0f}ms")
                _journal(acc.name, "BUY", sig.symbol, usd, msg, latency_ms, reason)
            else:
                log(f"[{acc.name}] BUY BLOCKED | {msg} | latency {latency_ms:.0f}ms")
        elif sig.action == "sell" and sig.amount is not None:
            log(
                f"[{acc.name}] PLAN SELL {sig.symbol.upper()} "
                f"amount={sig.amount} | why: {reason}"
            )
            ok, msg = execute_sell(
                pf, sig.symbol, sig.amount,
                source=settings.price_source, note=sig.reason,
            )
            save_for_account(pf, acc)
            latency_ms = (time.time() - signal_t0) * 1000.0
            if ok:
                filled_sells += 1
                log(f"[{acc.name}] FILLED SELL | {msg} | latency {latency_ms:.0f}ms")
                _journal(acc.name, "SELL", sig.symbol, sig.amount, msg, latency_ms, reason)
            else:
                log(f"[{acc.name}] SELL BLOCKED | {msg} | latency {latency_ms:.0f}ms")

    if buys or sells:
        log(
            f"[{acc.name}] Tick done in {(time.time()-tick_t0)*1000:.0f}ms | "
            f"filled {filled_buys} buy(s), {filled_sells} sell(s)"
        )
        try:
            from pnl_rates import compute_rates, format_rates_line, mark_session_start
            eq2 = pf.total_equity(prices) if prices else pf.cash
            mark_session_start(eq2, force=False)
            rates = compute_rates(eq2, pf.starting_balance)
            log(f"[{acc.name}] {format_rates_line(rates)}")
        except Exception:
            pass


if __name__ == "__main__":
    run_loop()
