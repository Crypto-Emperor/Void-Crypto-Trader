#!/usr/bin/env python3
"""
Void Crypto Trader - simulation + live-ready.

From VS Code terminal:

  python bot.py                  # interactive
  python bot.py start            # start auto-trader in background
  python bot.py stop             # stop auto-trader
  python bot.py status           # portfolio + auto-trader status
  python bot.py project          # quarterly profit projection
  python bot.py buy sol 200
  python bot.py sell sol 50%
  python bot.py set-capital 5000
  python bot.py reset

Auto-trader keeps running while you sleep (sim or live once FOMO is wired).
"""

from __future__ import annotations
import os
import sys
import subprocess
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
import click

from config import settings, PID_FILE, STOP_FLAG, LOG_FILE, STATE_FILE, BASE_DIR
from portfolio import Portfolio, execute_buy, execute_sell
from prices import get_price, get_prices
from projection import project_quarter

console = Console()


def is_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        return False
    if pid <= 0:
        return False
    # Windows + Unix process-alive check
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def print_banner():
    mode = "LIVE" if settings.is_live else "SIMULATION"
    running = "RUNNING" if is_running() else "stopped"
    console.print(
        Panel(
            f"[bold]Void Crypto Trader[/bold]\n"
            f"Mode: {mode}   Auto-trader: [bold]{running}[/bold]\n"
            f"Starting capital: ${settings.starting_balance:,.0f}   "
            f"Strategy: {settings.strategy}\n"
            f"Trade size: ${settings.trade_usd}   Symbols: {', '.join(settings.symbol_list)}",
            title="Void Crypto Trader",
            border_style="cyan",
        )
    )


def show_status(pf: Portfolio):
    symbols = list(set(list(pf.positions.keys()) + settings.symbol_list))
    prices = get_prices(symbols, settings.price_source)

    table = Table(title="Portfolio", box=box.ROUNDED)
    table.add_column("Asset", style="cyan")
    table.add_column("Amount", justify="right")
    table.add_column("Avg Entry", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("Value", justify="right")
    table.add_column("PnL $", justify="right")
    table.add_column("PnL %", justify="right")

    table.add_row("CASH", f"${pf.cash:,.2f}", "-", "-", f"${pf.cash:,.2f}", "-", "-")

    for sym, pos in pf.positions.items():
        price = prices.get(sym, 0.0)
        value = pos.market_value(price)
        pnl = pos.pnl(price)
        pct = pos.pnl_pct(price)
        style = "green" if pnl >= 0 else "red"
        table.add_row(
            sym.upper(),
            f"{pos.amount:.6f}",
            f"${pos.avg_entry:.6f}",
            f"${price:.6f}",
            f"${value:,.2f}",
            f"[{style}]{pnl:+,.2f}[/{style}]",
            f"[{style}]{pct:+.2f}%[/{style}]",
        )

    equity = pf.total_equity(prices)
    total_pnl = equity - pf.starting_balance
    total_pct = (total_pnl / pf.starting_balance * 100) if pf.starting_balance else 0
    style = "green" if total_pnl >= 0 else "red"

    console.print(table)
    console.print(
        f"\n[bold]Equity:[/bold] ${equity:,.2f}   "
        f"[bold]PnL:[/bold] [{style}]{total_pnl:+,.2f} ({total_pct:+.2f}%)[/{style}]"
    )
    if is_running():
        console.print(f"[green]Auto-trader is RUNNING[/green] (pid {PID_FILE.read_text().strip()})")
        console.print(f"[dim]Log: {LOG_FILE}[/dim]")
    else:
        console.print("[dim]Auto-trader is stopped.  python bot.py start[/dim]")


def show_trades(pf: Portfolio, limit: int = 15):
    if not pf.trades:
        console.print("[dim]No trades yet.[/dim]")
        return
    table = Table(title=f"Recent Trades (last {limit})", box=box.SIMPLE)
    table.add_column("Time")
    table.add_column("Side")
    table.add_column("Symbol")
    table.add_column("Amount", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("USD", justify="right")
    table.add_column("Mode")
    table.add_column("Note")
    for t in pf.trades[-limit:]:
        side = "green" if t.side == "buy" else "red"
        table.add_row(
            t.timestamp[:19],
            f"[{side}]{t.side.upper()}[/{side}]",
            t.symbol.upper(),
            f"{t.amount:.4f}",
            f"${t.price:.6f}",
            f"${t.usd_value:.2f}",
            t.mode,
            (t.note or "")[:40],
        )
    console.print(table)


def interactive(pf: Portfolio):
    print_banner()
    show_status(pf)
    console.print(
        "\n[bold]Commands:[/bold] buy | sell | status | trades | project | "
        "start | stop | set-capital | reset | quit\n"
    )
    while True:
        try:
            raw = console.input("[bold cyan]fomo>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye.")
            break
        if not raw:
            continue
        parts = raw.split()
        cmd = parts[0].lower()

        if cmd in ("q", "quit", "exit"):
            console.print("Bye.")
            break
        elif cmd == "status":
            show_status(pf)
        elif cmd == "trades":
            show_trades(pf)
        elif cmd == "project":
            show_projection()
        elif cmd == "price" and len(parts) >= 2:
            p = get_price(parts[1], settings.price_source)
            console.print(f"{parts[1].upper()} = ${p}" if p else "[red]no price[/red]")
        elif cmd == "buy" and len(parts) >= 3:
            try:
                usd = float(parts[2])
            except ValueError:
                console.print("[red]bad amount[/red]")
                continue
            ok, msg = execute_buy(pf, parts[1], usd, source=settings.price_source)
            console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
            pf = Portfolio.load()
            if ok:
                show_status(pf)
        elif cmd == "sell" and len(parts) >= 3:
            ok, msg = execute_sell(pf, parts[1], parts[2], source=settings.price_source)
            console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
            pf = Portfolio.load()
            if ok:
                show_status(pf)
        elif cmd == "start":
            do_start()
        elif cmd == "stop":
            do_stop()
        elif cmd == "set-capital" and len(parts) >= 2:
            do_set_capital(float(parts[1]))
            pf = Portfolio.load()
        elif cmd == "reset":
            if console.input("Reset portfolio? (y/N): ").strip().lower() == "y":
                pf = Portfolio(starting_balance=settings.starting_balance, cash=settings.starting_balance)
                pf.save()
                console.print("[yellow]Reset.[/yellow]")
                show_status(pf)
        elif cmd == "help":
            console.print(
                "buy sol 200 | sell sol 50% | status | trades | project\n"
                "start | stop | set-capital 5000 | reset | quit"
            )
        else:
            console.print("[red]Unknown. Type help[/red]")


def show_projection():
    p = project_quarter()
    table = Table(title="Quarterly Projection (~90 days)", box=box.ROUNDED)
    table.add_column("Scenario")
    table.add_column("Equity", justify="right")
    table.add_column("PnL", justify="right")
    table.add_row("Current", f"${p['current_equity']:,.2f}", "-")
    table.add_row("Base case", f"${p['quarter_base']:,.2f}", f"${p['quarter_base_pnl']:+,.2f}")
    table.add_row("Optimistic (~+1sigma)", f"${p['quarter_optimistic']:,.2f}", "-")
    table.add_row("Pessimistic (~-1sigma)", f"${p['quarter_pessimistic']:,.2f}", "-")
    console.print(table)
    console.print(f"[dim]Expected annual return used: {p['expected_annual_return_used']*100:.1f}%[/dim]")
    if p["realized_annualized"] is not None:
        console.print(f"[dim]Realized annualized so far: {p['realized_annualized']*100:.1f}%[/dim]")
    console.print(f"[dim]{p['note']}[/dim]")





def do_start(foreground: bool = False, follow: bool = True):
    """Start auto-trader. Default: stream every log/trade line in this terminal."""
    if is_running():
        console.print("[yellow]Already running.[/yellow]")
        return
    if STOP_FLAG.exists():
        try:
            STOP_FLAG.unlink()
        except Exception:
            pass

    cmd = [sys.executable, "-u", str(BASE_DIR / "auto_trader.py")]
    cwd = str(BASE_DIR)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # Foreground OR live feed: keep process attached and stream stdout here
    if foreground or follow:
        console.print("[cyan]Auto-trader running - every trade prints below[/cyan]")
        console.print("[dim]Ctrl+C to stop[/dim]")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
        except Exception as e:
            console.print(f"[red]Failed to start: {e}[/red]")
            return

        # Wait briefly for PID file
        for _ in range(20):
            time.sleep(0.1)
            if is_running() or (proc.poll() is not None):
                break

        if proc.poll() is not None and not is_running():
            out = proc.stdout.read() if proc.stdout else ""
            console.print("[red]Auto-trader exited immediately[/red]")
            if out:
                console.print(out)
            return

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                text = line.rstrip()
                if not text:
                    continue
                up = text.upper()
                if "TRADE BUY" in up or "TRADE SELL" in up or " SIGNAL " in up:
                    console.print(f"[bold]{text}[/bold]")
                else:
                    console.print(text)
            # process ended
            code = proc.wait()
            console.print(f"[dim]Auto-trader exited (code {code})[/dim]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping...[/yellow]")
            try:
                STOP_FLAG.write_text("1", encoding="utf-8")
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass
            for _ in range(20):
                time.sleep(0.2)
                if proc.poll() is not None:
                    break
            if proc.poll() is None:
                try:
                    if sys.platform == "win32":
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(proc.pid)],
                            capture_output=True,
                        )
                    else:
                        proc.kill()
                except Exception:
                    pass
            clear_stale()
            console.print("[green]Stopped.[/green]")
        return

    # Detached background (no live terminal feed)
    try:
        log_f = open(LOG_FILE, "a", encoding="utf-8")
    except Exception as e:
        console.print(f"[red]Cannot open log file {LOG_FILE}: {e}[/red]")
        return

    popen_kwargs = {
        "args": cmd,
        "cwd": cwd,
        "env": env,
        "stdout": log_f,
        "stderr": log_f,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        flags = 0
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        popen_kwargs["creationflags"] = flags
        popen_kwargs["close_fds"] = False
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(**popen_kwargs)
    except Exception as e:
        console.print(f"[red]Failed to spawn auto_trader: {e}[/red]")
        try:
            log_f.close()
        except Exception:
            pass
        return

    for _ in range(20):
        time.sleep(0.25)
        if is_running():
            console.print(f"[green]Auto-trader started (pid {proc.pid})[/green]")
            console.print(f"[dim]Log -> {LOG_FILE}[/dim]")
            console.print("[dim]Detached. Stop with: python bot.py stop[/dim]")
            try:
                log_f.close()
            except Exception:
                pass
            return

    console.print("[red]Failed to start - auto_trader exited early.[/red]")
    console.print("[yellow]Try: python bot.py start[/yellow]")
    console.print(f"[yellow]Or: {sys.executable} -u auto_trader.py[/yellow]")
    try:
        if LOG_FILE.exists():
            tail = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-30:]
            for line in tail:
                console.print(f"[dim]{line}[/dim]")
    except Exception:
        pass
    try:
        log_f.close()
    except Exception:
        pass



def do_stop():
    if not is_running():
        console.print("[dim]Not running.[/dim]")
        clear_stale()
        return
    STOP_FLAG.write_text("1")
    console.print("[yellow]Stop signal sent...[/yellow]")
    for _ in range(20):
        time.sleep(0.5)
        if not is_running():
            console.print("[green]Stopped.[/green]")
            return
    # force
    try:
        pid = int(PID_FILE.read_text().strip())
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)
    except Exception:
        pass
    clear_stale()
    console.print("[green]Force-stopped.[/green]")


def clear_stale():
    if PID_FILE.exists():
        PID_FILE.unlink(missing_ok=True)
    if STOP_FLAG.exists():
        STOP_FLAG.unlink(missing_ok=True)


def do_set_capital(amount: float):
    """Reset starting capital and cash (sim). For live, LIVE_MAX_USD limits risk."""
    if amount <= 0:
        console.print("[red]Must be > 0[/red]")
        return
    # update .env if present
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        out = []
        found = False
        for line in lines:
            if line.startswith("STARTING_BALANCE="):
                out.append(f"STARTING_BALANCE={amount}")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"STARTING_BALANCE={amount}")
        env_path.write_text("\n".join(out) + "\n")
    pf = Portfolio.load()
    # scale cash relative to old start or just set clean
    pf.starting_balance = amount
    pf.cash = amount
    pf.positions = {}
    pf.trades = []
    pf.save()
    console.print(f"[green]Starting capital set to ${amount:,.2f} and portfolio reset.[/green]")
    console.print("[dim]Also update STARTING_BALANCE in .env permanently if needed.[/dim]")


# ---------- Click CLI ----------

@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        pf = Portfolio.load()
        pf.mode = "live" if settings.is_live else "sim"
        interactive(pf)


@cli.command()
def status():
    print_banner()
    show_status(Portfolio.load())


@cli.command()
@click.option("--foreground", "-f", is_flag=True, help="Run worker in this terminal")
@click.option("--detach", "-d", is_flag=True, help="Background only (no live trade feed)")
def start(foreground, detach):
    """Start auto-trader. Default: show every trade in this terminal."""
    do_start(foreground=foreground, follow=not detach)


@cli.command()
def stop():
    """Stop auto-trader."""
    do_stop()


@cli.command()
def project():
    """Show quarterly profit projection."""
    show_projection()


@cli.command()
@click.argument("symbol")
@click.argument("usd_amount", type=float)
def buy(symbol, usd_amount):
    pf = Portfolio.load()
    pf.mode = "live" if settings.is_live else "sim"
    ok, msg = execute_buy(pf, symbol, usd_amount, source=settings.price_source)
    console.print(f"[{'green' if ok else 'red'}]{msg}[/]")


@cli.command()
@click.argument("symbol")
@click.argument("amount")
def sell(symbol, amount):
    pf = Portfolio.load()
    pf.mode = "live" if settings.is_live else "sim"
    ok, msg = execute_sell(pf, symbol, amount, source=settings.price_source)
    console.print(f"[{'green' if ok else 'red'}]{msg}[/]")


@cli.command("set-capital")
@click.argument("amount", type=float)
def set_capital(amount):
    """Set starting capital (sim) and reset portfolio."""
    do_set_capital(amount)


@cli.command()
@click.confirmation_option(prompt="Reset portfolio to starting balance?")
def reset():
    pf = Portfolio(starting_balance=settings.starting_balance, cash=settings.starting_balance)
    pf.save()
    console.print("[yellow]Portfolio reset.[/yellow]")


@cli.command()
def trades():
    show_trades(Portfolio.load(), 25)


@cli.command()
@click.argument("symbol")
def price(symbol):
    p = get_price(symbol, settings.price_source)
    console.print(f"{symbol.upper()} = ${p}" if p else "[red]no price[/red]")



@cli.command("check")
@click.argument("symbol_or_mint")
@click.option("--chain", default=None, help="solana | ethereum | base | bsc ...")
def check_token(symbol_or_mint, chain):
    """Run GoPlus honeypot / sellability check on a symbol or mint."""
    from security import check_before_buy
    from config import settings
    ch = chain or settings.default_chain
    allowed, msg, result = check_before_buy(
        symbol_or_mint, chain=ch, strict=settings.honeypot_strict
    )
    style = "green" if allowed else "red"
    console.print(f"[{style}]{'ALLOW' if allowed else 'BLOCK'}[/{style}] {symbol_or_mint}: {msg}")
    if result:
        console.print(f"  honeypot={result.is_honeypot}  can_sell={result.can_sell}")
        if result.buy_tax is not None:
            console.print(f"  buy_tax={result.buy_tax}")
        if result.sell_tax is not None:
            console.print(f"  sell_tax={result.sell_tax}")
        if result.risk_notes:
            console.print(f"  notes: {', '.join(result.risk_notes)}")



@cli.command("accounts")
def accounts_cmd():
    """List trading accounts (max 5)."""
    from accounts import load_accounts
    table = Table(title="Accounts (max 5)", box=box.ROUNDED)
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Mode")
    table.add_column("Enabled")
    table.add_column("Capital", justify="right")
    table.add_column("Strategy")
    for a in load_accounts():
        table.add_row(
            a.id, a.name, a.mode, "yes" if a.enabled else "no",
            f"${a.starting_balance:,.0f}", a.strategy or settings.strategy,
        )
    console.print(table)


@cli.command("add-account")
@click.argument("name")
@click.option("--capital", default=10000.0, type=float)
@click.option("--mode", default="sim", type=str)
def add_account_cmd(name, capital, mode):
    """Add an account (up to 5 total)."""
    from accounts import add_account, MAX_ACCOUNTS
    try:
        a = add_account(name, starting_balance=capital, mode=mode)
        console.print(f"[green]Added account id={a.id} name={a.name} capital=${a.starting_balance:,.0f}[/green]")
    except ValueError as e:
        console.print(f"[red]{e}[/red]")


@cli.command("leaderboard")
@click.option("--window", default=None, help="24h | 7d | 30d | all")
@click.option("--limit", default=15, type=int)
def leaderboard_cmd(window, limit):
    """Show FOMO leaderboard (live via FOMOAPI_KEY or demo)."""
    from leaderboard import format_leaderboard, top_token_consensus
    from config import settings as s
    w = window or s.leaderboard_window
    console.print(format_leaderboard(w, limit))
    cons = top_token_consensus(w, top_n_traders=s.leaderboard_top_n, min_mentions=1)
    if cons:
        console.print("\n[bold]Token consensus (top traders):[/bold]")
        for tok, n in sorted(cons.items(), key=lambda x: -x[1])[:15]:
            console.print(f"  {tok}: {n} mentions")



@cli.command("follow")
@click.argument("handle")
@click.option("--account", default=None, help="Limit copy to this account id")
def follow_cmd(handle, account):
    """Follow a FOMO trader handle for copy trading."""
    from copy_trading import follow_trader, load_follows
    ids = [account] if account else []
    cfg = follow_trader(handle, account_ids=ids)
    console.print(f"[green]Now following @{cfg.handle}[/green] (enabled={cfg.enabled})")
    console.print("[dim]Set STRATEGY=fomo_copy in .env and run: python bot.py start[/dim]")


@cli.command("unfollow")
@click.argument("handle")
def unfollow_cmd(handle):
    """Stop following a FOMO trader."""
    from copy_trading import unfollow_trader
    if unfollow_trader(handle):
        console.print(f"[yellow]Unfollowed @{handle.lstrip('@')}[/yellow]")
    else:
        console.print("[dim]Not in follow list.[/dim]")


@cli.command("follows")
def follows_cmd():
    """List FOMO handles you are copying."""
    from copy_trading import load_follows
    rows = load_follows()
    if not rows:
        console.print("[dim]No follows. python bot.py follow <handle>[/dim]")
        return
    table = Table(title="Copy follows", box=box.ROUNDED)
    table.add_column("Handle")
    table.add_column("Enabled")
    table.add_column("Accounts")
    table.add_column("Max USD/trade")
    for f in rows:
        table.add_row(
            f"@{f.handle}",
            "yes" if f.enabled else "no",
            ",".join(f.account_ids) if f.account_ids else "all",
            str(f.max_usd_per_trade or "-"),
        )
    console.print(table)


@cli.command("copy-preview")
@click.argument("handle")
def copy_preview_cmd(handle):
    """Show recent trades/balances for a FOMO handle (no execution)."""
    from copy_trading import fetch_user_trades, fetch_user_balances
    trades = fetch_user_trades(handle, limit=10)
    bals = fetch_user_balances(handle)
    console.print(f"[bold]@{handle.lstrip('@')} recent trades:[/bold]")
    if not trades:
        console.print("[dim]No trades returned (need FOMOAPI capture / key, or handle unknown)[/dim]")
    for t in trades[:10]:
        console.print(
            f"  {t.status:6} {t.symbol:12} ~${t.usd_value:,.0f}  id={t.trade_id[:24]}"
        )
    console.print(f"[bold]Holdings count:[/bold] {len(bals)}")
    for h in bals[:8]:
        tok = h.get("token") or {}
        sym = tok.get("symbol") or h.get("symbol") or "?"
        val = h.get("valueUsd") or h.get("value") or 0
        console.print(f"  {sym}: ${float(val):,.2f}")



@cli.command("who-up")
@click.option("--window", default=None)
@click.option("--limit", default=15, type=int)
@click.option("--min-pnl", default=0.0, type=float)
def who_up_cmd(window, limit, min_pnl):
    """Scan FOMO leaderboard for traders who are currently UP (positive PnL)."""
    from leaderboard import traders_who_are_up
    from config import settings as s
    w = window or s.leaderboard_window
    rows = traders_who_are_up(w, limit=limit, min_pnl=min_pnl)
    if not rows:
        console.print("[dim]No traders with positive PnL returned.[/dim]")
        return
    table = Table(title=f"Who is UP ({w})", box=box.ROUNDED)
    table.add_column("#")
    table.add_column("Handle")
    table.add_column("PnL $", justify="right")
    table.add_column("Volume", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Top tokens")
    for t in rows:
        toks = ",".join(t.top_tokens[:3]) if t.top_tokens else "-"
        table.add_row(
            str(t.rank),
            f"@{t.handle}",
            f"${t.pnl_usd:,.0f}",
            f"${t.volume_usd:,.0f}",
            str(t.trades),
            toks,
        )
    console.print(table)
    console.print(
        "[dim]STRATEGY=top_accounts_copy copies these traders' new buys/sells[/dim]"
    )



@cli.command("predict")
@click.option("--symbol", default=None, help="Focus one symbol")
def predict_cmd(symbol):
    """Predict likely next moves from UP-trader flow + momentum."""
    from predict import predict_tokens, forecast_portfolio
    from portfolio import Portfolio

    pf = Portfolio.load()
    if symbol:
        preds = predict_tokens([symbol], portfolio=pf)
    else:
        fc = forecast_portfolio(pf)
        console.print(
            Panel(
                f"[bold]Horizon:[/bold] {fc.horizon}\n"
                f"Current equity: ${fc.current_equity:,.2f}\n"
                f"Expected: ${fc.expected_equity:,.2f} ({fc.expected_pnl:+,.2f})\n"
                f"Band: ${fc.low_equity:,.2f} -> ${fc.high_equity:,.2f}",
                title="Portfolio forecast",
                border_style="cyan",
            )
        )
        for n in fc.notes:
            console.print(f"  • {n}")
        preds = fc.token_predictions

    table = Table(title="Token predictions", box=box.ROUNDED)
    table.add_column("Symbol")
    table.add_column("Bias")
    table.add_column("Conf", justify="right")
    table.add_column("Action")
    table.add_column("Leaders B/S", justify="right")
    table.add_column("Mom %", justify="right")
    table.add_column("Drivers")
    for p in preds[:20]:
        bias_style = {"bullish": "green", "bearish": "red", "neutral": "yellow"}.get(p.bias, "white")
        mom = f"{p.momentum_pct:+.1f}" if p.momentum_pct is not None else "-"
        table.add_row(
            p.symbol.upper(),
            f"[{bias_style}]{p.bias}[/{bias_style}]",
            f"{p.confidence}%",
            p.suggested_action,
            f"{p.leader_buy_count}/{p.leader_sell_count}",
            mom,
            "; ".join(p.drivers)[:50],
        )
    console.print(table)
    console.print(
        "[dim]Heuristic only (FOMO UP-trader flow + momentum). Not financial advice.[/dim]"
    )



@cli.command("pilot")
@click.option("--limit", default=12, type=int)
def pilot_cmd(limit):
    """TradingPilot-style signal board: bias, confidence, entry/SL/TP."""
    from pilot import pilot_dashboard
    dash = pilot_dashboard(limit=limit)
    console.print(
        Panel(
            f"[bold]TradingPilot-style board[/bold]  ·  {dash['generated_at']}\n"
            f"Longs: {len(dash['longs'])}   Shorts: {len(dash['shorts'])}   "
            f"Flips: {len(dash['flips'])}",
            title="Pilot",
            border_style="magenta",
        )
    )
    table = Table(title="Top signals", box=box.ROUNDED)
    table.add_column("#")
    table.add_column("Symbol")
    table.add_column("Side")
    table.add_column("Bias", justify="right")
    table.add_column("Conf", justify="right")
    table.add_column("Entry", justify="right")
    table.add_column("SL", justify="right")
    table.add_column("TP", justify="right")
    table.add_column("R:R", justify="right")
    table.add_column("Drivers")
    for i, s in enumerate(dash["signals"], 1):
        style = {"long": "green", "short": "red", "neutral": "yellow"}.get(s.side, "white")
        flip = " ↺" if s.flipped else ""
        table.add_row(
            str(i),
            s.symbol.upper() + flip,
            f"[{style}]{s.side}[/{style}]",
            f"{s.bias_score:+.2f}",
            f"{s.confidence:.0%}",
            f"${s.entry:,.4g}" if s.entry else "-",
            f"${s.stop_loss:,.4g}" if s.stop_loss else "-",
            f"${s.take_profit:,.4g}" if s.take_profit else "-",
            f"{s.risk_reward}" if s.risk_reward else "-",
            "; ".join(s.drivers)[:42],
        )
    console.print(table)
    if dash["movers"]:
        console.print("\n[bold]Biggest movers (watchlist momentum)[/bold]")
        for sym, mom, px in dash["movers"][:6]:
            st = "green" if mom >= 0 else "red"
            console.print(f"  [{st}]{sym.upper():8} {mom:+.2f}%[/{st}]  px={px}")
    if dash["flips"]:
        console.print("\n[bold]Signal flips[/bold]")
        for s in dash["flips"]:
            console.print(f"  {s.symbol.upper()}: {s.prev_side} -> {s.side} (bias {s.bias_score:+.2f})")
    console.print(
        "[dim]Heuristic FOMO+momentum board. Not the same as tradingpilot.ai stocks product. Not advice.[/dim]"
    )


@cli.command("signals")
@click.pass_context
def signals_cmd(ctx):
    """Alias for pilot signal board."""
    ctx.invoke(pilot_cmd)



@cli.command("free-board")
@click.option("--limit", default=12, type=int)
def free_board_cmd(limit):
    """Free market board (CoinPaprika / CoinGecko / DexScreener)."""
    from free_api import free_leaderboard, free_hot_tokens, data_source
    try:
        from free_api import enriched_hot_tokens as free_hot_tokens
    except Exception:
        pass
    console.print(Panel(
        f"[bold]Free data source[/bold]: {data_source()}\n"
        "CoinPaprika + CoinGecko trending + DexScreener - $0 credits, large token set.",
        title="free-api",
        border_style="green",
    ))
    board = free_leaderboard("24h", limit=limit)
    table = Table(title="Synthetic 'who is up' (free)", box=box.ROUNDED)
    table.add_column("#")
    table.add_column("Handle")
    table.add_column("PnL proxy", justify="right")
    table.add_column("Vol", justify="right")
    table.add_column("Tokens")
    for t in board:
        table.add_row(
            str(t.rank),
            t.handle,
            f"${t.pnl_usd:,.0f}",
            f"${t.volume_usd:,.0f}",
            ",".join(t.top_tokens[:3]) or "-",
        )
    console.print(table)
    hot = free_hot_tokens(8)
    console.print("\n[bold]Hot tokens[/bold]")
    for h in hot:
        console.print(
            f"  {h['symbol'].upper():10} {h['change_h24']:+.1f}%  "
            f"vol ${h['volume_h24']:,.0f}  px={h['price_usd']}"
        )



@cli.command("wallet-copy")
@click.option("--test", is_flag=True, help="RPC self-test only")
@click.option("--wallet", default=None, help="Leader wallet override")
def wallet_copy_cmd(test, wallet):
    """1s on-chain copy of a Solana leader wallet (sim by default)."""
    import asyncio
    from wallet_copy import self_test, scan_leader_every_second
    if test:
        ok = self_test(wallet)
        raise SystemExit(0 if ok else 1)
    asyncio.run(scan_leader_every_second(wallet))


if __name__ == "__main__":
    cli()
