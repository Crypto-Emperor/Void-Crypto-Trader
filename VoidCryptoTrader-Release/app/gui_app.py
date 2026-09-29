#!/usr/bin/env python3
"""
Void Exchange — stock-exchange style paper trader UI
Inspired by trading terminals / Roblox Stock Exchange layout
(left market list, center chart, right portfolio + trade ticket)
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
import traceback
from datetime import datetime
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = app_dir()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    sys.path.insert(0, str(sys._MEIPASS))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "keys" / "api_keys.env", override=True)
except Exception:
    pass

import tkinter as tk
from tkinter import ttk, messagebox

HAS_MPL = False
try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import matplotlib as mpl
    HAS_MPL = True
except Exception:
    pass

# Fixed market board (not random free-API junk)
MARKET = [
    # Core
    ("SOL", "Solana", 118.75),
    ("BTC", "Bitcoin", 95000.0),
    ("ETH", "Ethereum", 3400.0),
    # Solana ecosystem / FOMO-style
    ("JUP", "Jupiter", 0.81),
    ("RAY", "Raydium", 2.95),
    ("BONK", "Bonk", 0.000021),
    ("WIF", "dogwifhat", 1.42),
    ("POPCAT", "Popcat", 0.48),
    ("PNUT", "Peanut", 0.28),
    ("GOAT", "Goatseus", 0.45),
    ("TRUMP", "TRUMP", 9.2),
    ("AI16Z", "ai16z", 0.95),
    ("MEW", "cat in a dogs world", 0.0048),
    ("WEN", "Wen", 0.00009),
    ("PYTH", "Pyth", 0.38),
]

# Theme — terminal / exchange floor
C = {
    "bg": "#0a0e14",
    "panel": "#0d1219",
    "card": "#121820",
    "border": "#1e2a3a",
    "text": "#e7ecf3",
    "muted": "#8b9bb0",
    "green": "#3dd68c",
    "red": "#f07178",
    "yellow": "#e6b450",
    "blue": "#59c2ff",
    "header": "#0b1018",
    "row": "#0f1520",
    "row_alt": "#121a26",
    "select": "#1a2740",
}

if HAS_MPL:
    mpl.rcParams.update({
        "axes.facecolor": C["card"],
        "figure.facecolor": C["panel"],
        "text.color": C["text"],
        "axes.labelcolor": C["muted"],
        "xtick.color": C["muted"],
        "ytick.color": C["muted"],
        "axes.edgecolor": C["border"],
    })


class ExchangeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Void Exchange")
        self.geometry("1280x780")
        self.minsize(1100, 650)
        self.configure(bg=C["bg"])

        self._prices: dict[str, float] = {s: p for s, _, p in MARKET}
        self._prev: dict[str, float] = dict(self._prices)
        self._hist: dict[str, list] = {s: [(time.time(), p)] for s, p in self._prices.items()}
        self._selected = "SOL"
        self._busy = False
        self._stop = False
        self._bot_thread = None
        self._bot_stop = threading.Event()

        self.var_cash = tk.StringVar(value="$0")
        self.var_equity = tk.StringVar(value="$0")
        self.var_pnl = tk.StringVar(value="$0")
        self.var_bot = tk.StringVar(value="BOT OFF")
        self.var_status = tk.StringVar(value="Paper market")
        self.var_ticker = tk.StringVar(value="SOL")
        self.var_last = tk.StringVar(value="—")
        self.var_chg = tk.StringVar(value="—")
        self.trade_qty = tk.StringVar(value="10")

        self._build()
        self.after(200, self.refresh_async)
        self.after(4000, self._price_tick)
        self.after(8000, self._loop)

    # ---------- UI ----------
    def _build(self):
        # TOP BAR
        top = tk.Frame(self, bg=C["header"], height=48)
        top.pack(fill=tk.X)
        top.pack_propagate(False)
        tk.Label(top, text="  VOID EXCHANGE", fg=C["blue"], bg=C["header"],
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT, padx=8)
        tk.Label(top, text="PAPER", fg=C["yellow"], bg=C["header"],
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=6)

        for label, var in (("CASH", self.var_cash), ("EQUITY", self.var_equity), ("P&L", self.var_pnl)):
            f = tk.Frame(top, bg=C["header"])
            f.pack(side=tk.LEFT, padx=14)
            tk.Label(f, text=label, fg=C["muted"], bg=C["header"], font=("Segoe UI", 7)).pack(anchor=tk.W)
            tk.Label(f, textvariable=var, fg=C["text"], bg=C["header"],
                     font=("Consolas", 12, "bold")).pack(anchor=tk.W)

        tk.Label(top, textvariable=self.var_bot, fg=C["green"], bg=C["header"],
                 font=("Segoe UI", 9, "bold")).pack(side=tk.RIGHT, padx=12)
        tk.Label(top, textvariable=self.var_status, fg=C["muted"], bg=C["header"],
                 font=("Segoe UI", 8)).pack(side=tk.RIGHT, padx=8)

        # BUTTONS
        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill=tk.X, padx=10, pady=6)
        for text, cmd, fg in [
            ("START BOT", self.start_bot, C["green"]),
            ("STOP BOT", self.stop_bot, C["red"]),
            ("SETTINGS", self.open_settings, C["blue"]),
            ("REFRESH", self.refresh_async, C["muted"]),
        ]:
            b = tk.Label(bar, text=f"  {text}  ", fg=fg, bg=C["card"], font=("Segoe UI", 9, "bold"),
                         cursor="hand2", padx=4, pady=6)
            b.pack(side=tk.LEFT, padx=4)
            b.bind("<Button-1>", lambda e, c=cmd: c())
            b.bind("<Enter>", lambda e, w=b: w.configure(bg=C["border"]))
            b.bind("<Leave>", lambda e, w=b: w.configure(bg=C["card"]))

        # MAIN 3 COLUMNS
        main = tk.Frame(self, bg=C["bg"])
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        # --- LEFT: market list ---
        left = tk.Frame(main, bg=C["panel"], width=280)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        left.pack_propagate(False)
        tk.Label(left, text="MARKET", fg=C["muted"], bg=C["panel"],
                 font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, padx=10, pady=(8, 4))

        hdr = tk.Frame(left, bg=C["border"])
        hdr.pack(fill=tk.X, padx=4)
        for t, w in (("TICKER", 8), ("PRICE", 10), ("% 24h", 8)):
            tk.Label(hdr, text=t, fg=C["muted"], bg=C["border"], width=w,
                     font=("Segoe UI", 7, "bold")).pack(side=tk.LEFT, padx=2, pady=3)

        self.mkt_canvas = tk.Canvas(left, bg=C["panel"], highlightthickness=0)
        msb = ttk.Scrollbar(left, orient="vertical", command=self.mkt_canvas.yview)
        self.mkt_frame = tk.Frame(self.mkt_canvas, bg=C["panel"])
        self.mkt_canvas.create_window((0, 0), window=self.mkt_frame, anchor="nw")
        self.mkt_canvas.configure(yscrollcommand=msb.set)
        msb.pack(side=tk.RIGHT, fill=tk.Y)
        self.mkt_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.mkt_frame.bind("<Configure>", lambda e: self.mkt_canvas.configure(
            scrollregion=self.mkt_canvas.bbox("all")))
        self._mkt_rows: dict[str, dict] = {}
        self._build_market_rows()

        # --- CENTER: chart + trade ---
        center = tk.Frame(main, bg=C["panel"])
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)

        ch = tk.Frame(center, bg=C["panel"])
        ch.pack(fill=tk.X, padx=10, pady=8)
        tk.Label(ch, textvariable=self.var_ticker, fg=C["text"], bg=C["panel"],
                 font=("Segoe UI", 18, "bold")).pack(side=tk.LEFT)
        tk.Label(ch, textvariable=self.var_last, fg=C["blue"], bg=C["panel"],
                 font=("Consolas", 18, "bold")).pack(side=tk.LEFT, padx=12)
        tk.Label(ch, textvariable=self.var_chg, fg=C["green"], bg=C["panel"],
                 font=("Consolas", 12, "bold")).pack(side=tk.LEFT)

        if HAS_MPL:
            self.fig = Figure(figsize=(6, 3.2), dpi=100)
            self.ax = self.fig.add_subplot(111)
            self.cv = FigureCanvasTkAgg(self.fig, master=center)
            self.cv.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        else:
            tk.Label(center, text="Install matplotlib for charts", fg=C["red"], bg=C["panel"]).pack(pady=40)

        # Trade ticket
        ticket = tk.Frame(center, bg=C["card"], highlightbackground=C["border"], highlightthickness=1)
        ticket.pack(fill=tk.X, padx=8, pady=8)
        tk.Label(ticket, text="ORDER TICKET (paper)", fg=C["muted"], bg=C["card"],
                 font=("Segoe UI", 8, "bold")).pack(anchor=tk.W, padx=10, pady=(8, 2))
        row = tk.Frame(ticket, bg=C["card"])
        row.pack(fill=tk.X, padx=10, pady=6)
        tk.Label(row, text="USD amount", fg=C["muted"], bg=C["card"], font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Entry(row, textvariable=self.trade_qty, width=12, bg=C["bg"], fg=C["text"],
                 insertbackground=C["text"], relief=tk.FLAT, font=("Consolas", 11)).pack(side=tk.LEFT, padx=8)
        buy = tk.Label(row, text="  BUY  ", fg=C["bg"], bg=C["green"], font=("Segoe UI", 10, "bold"),
                       cursor="hand2", padx=8, pady=4)
        buy.pack(side=tk.LEFT, padx=4)
        buy.bind("<Button-1>", lambda e: self.do_trade("buy"))
        sell = tk.Label(row, text="  SELL  ", fg=C["bg"], bg=C["red"], font=("Segoe UI", 10, "bold"),
                        cursor="hand2", padx=8, pady=4)
        sell.pack(side=tk.LEFT, padx=4)
        sell.bind("<Button-1>", lambda e: self.do_trade("sell"))
        tk.Label(ticket, text="Buy = spend $ amount · Sell = % of position (use 25/50/100)",
                 fg=C["muted"], bg=C["card"], font=("Segoe UI", 7)).pack(anchor=tk.W, padx=10, pady=(0, 8))

        # --- RIGHT: portfolio ---
        right = tk.Frame(main, bg=C["panel"], width=300)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        right.pack_propagate(False)
        tk.Label(right, text="PORTFOLIO", fg=C["muted"], bg=C["panel"],
                 font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, padx=10, pady=(8, 4))

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Ex.Treeview", background=C["card"], fieldbackground=C["card"],
                        foreground=C["text"], rowheight=26, borderwidth=0, font=("Consolas", 9))
        style.configure("Ex.Treeview.Heading", background=C["border"], foreground=C["muted"],
                        font=("Segoe UI", 8, "bold"))
        style.map("Ex.Treeview", background=[("selected", C["select"])])
        self.tree = ttk.Treeview(right, columns=("sym", "val", "pnl"), show="headings",
                                 height=12, style="Ex.Treeview")
        self.tree.heading("sym", text="Asset")
        self.tree.heading("val", text="Value")
        self.tree.heading("pnl", text="PnL")
        self.tree.column("sym", width=70, anchor=tk.W)
        self.tree.column("val", width=90, anchor=tk.E)
        self.tree.column("pnl", width=80, anchor=tk.E)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._on_port_select)

        tk.Label(right, text="ACTIVITY", fg=C["muted"], bg=C["panel"],
                 font=("Segoe UI", 8, "bold")).pack(anchor=tk.W, padx=10, pady=(6, 0))
        self.log = tk.Text(right, height=8, bg=C["card"], fg=C["muted"], relief=tk.FLAT,
                           font=("Consolas", 8), insertbackground=C["text"])
        self.log.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._log("Welcome to Void Exchange (paper)")
        self._log("Click a ticker on the left to chart it")
        self.select_ticker("SOL")

    def _build_market_rows(self):
        for w in self.mkt_frame.winfo_children():
            w.destroy()
        self._mkt_rows.clear()
        for i, (sym, name, _) in enumerate(MARKET):
            bg = C["row"] if i % 2 == 0 else C["row_alt"]
            row = tk.Frame(self.mkt_frame, bg=bg, cursor="hand2")
            row.pack(fill=tk.X, pady=1)
            t_sym = tk.Label(row, text=sym, fg=C["text"], bg=bg, width=8, anchor=tk.W,
                             font=("Consolas", 9, "bold"))
            t_px = tk.Label(row, text="—", fg=C["text"], bg=bg, width=10, anchor=tk.E,
                            font=("Consolas", 9))
            t_ch = tk.Label(row, text="—", fg=C["muted"], bg=bg, width=8, anchor=tk.E,
                            font=("Consolas", 9))
            t_sym.pack(side=tk.LEFT, padx=2, pady=4)
            t_px.pack(side=tk.LEFT, padx=2)
            t_ch.pack(side=tk.LEFT, padx=2)
            for w in (row, t_sym, t_px, t_ch):
                w.bind("<Button-1>", lambda e, s=sym: self.select_ticker(s))
            self._mkt_rows[sym] = {"row": row, "px": t_px, "ch": t_ch, "bg": bg}

    def _log(self, msg: str):
        try:
            self.log.insert(tk.END, f"[{datetime.now():%H:%M:%S}] {msg}\n")
            self.log.see(tk.END)
        except Exception:
            pass

    def select_ticker(self, sym: str):
        sym = sym.upper()
        self._selected = sym
        self.var_ticker.set(sym)
        px = self._prices.get(sym, 0)
        self.var_last.set(self._fmt_px(px))
        # highlight
        for s, widgets in self._mkt_rows.items():
            bg = C["select"] if s == sym else widgets["bg"]
            widgets["row"].configure(bg=bg)
            for k in ("px", "ch"):
                widgets[k].configure(bg=bg)
            # recolor text widgets - need child labels
            for child in widgets["row"].winfo_children():
                child.configure(bg=bg)
        self._draw_chart()
        self._log(f"Selected {sym}")

    def _on_port_select(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        sym = self.tree.item(sel[0], "values")[0]
        if sym and sym != "CASH":
            self.select_ticker(sym)

    def _fmt_px(self, px: float) -> str:
        if px >= 1000:
            return f"${px:,.0f}"
        if px >= 1:
            return f"${px:,.2f}"
        if px >= 0.01:
            return f"${px:.4f}"
        return f"${px:.6g}"

    def _update_market_list(self):
        for sym, _, _ in MARKET:
            px = self._prices.get(sym, 0)
            prev = self._prev.get(sym, px) or px
            chg = ((px - prev) / prev * 100) if prev else 0
            w = self._mkt_rows.get(sym)
            if not w:
                continue
            w["px"].configure(text=self._fmt_px(px))
            color = C["green"] if chg >= 0 else C["red"]
            w["ch"].configure(text=f"{chg:+.2f}%", fg=color)
            if sym == self._selected:
                self.var_last.set(self._fmt_px(px))
                self.var_chg.set(f"{chg:+.2f}%")
                # update chg label color via status - store on widget
                for child in self.winfo_children():
                    pass

    def _draw_chart(self):
        if not HAS_MPL:
            return
        sym = self._selected
        series = self._hist.get(sym, [])
        self.ax.clear()
        self.ax.set_facecolor(C["card"])
        if len(series) >= 2:
            t0 = series[0][0]
            xs = [(t - t0) / 60 for t, _ in series]
            ys = [p for _, p in series]
            color = C["green"] if ys[-1] >= ys[0] else C["red"]
            self.ax.plot(xs, ys, color=color, lw=2)
            self.ax.fill_between(xs, ys, min(ys), color=color, alpha=0.12)
        elif len(series) == 1:
            y = series[0][1]
            self.ax.plot([0, 1, 2], [y * 0.997, y, y * 1.001], color=C["blue"], lw=2)
        self.ax.set_title(sym, color=C["muted"], fontsize=9, loc="left")
        self.ax.tick_params(labelsize=7, colors=C["muted"])
        self.fig.tight_layout()
        self.cv.draw_idle()

    # ---------- data ----------
    def _price_tick(self):
        """Slight simulated drift so list/% changes feel alive offline."""
        if self._stop:
            return
        import random
        for sym in list(self._prices.keys()):
            self._prev[sym] = self._prices[sym]
            jitter = 1 + random.uniform(-0.004, 0.004)
            self._prices[sym] = max(self._prices[sym] * jitter, 1e-12)
            self._hist.setdefault(sym, []).append((time.time(), self._prices[sym]))
            self._hist[sym] = self._hist[sym][-120:]
        self._update_market_list()
        if self._selected:
            self._draw_chart()
        self.after(4000, self._price_tick)

    def refresh_async(self):
        if self._busy:
            return
        self._busy = True

        def work():
            err, data = None, None
            try:
                data = self._collect()
            except Exception as e:
                err = str(e)
            self.after(0, lambda: self._apply(data, err))

        threading.Thread(target=work, daemon=True).start()

    def _collect(self) -> dict:
        from accounts import enabled_accounts
        from portfolio import load_for_account
        accs = enabled_accounts()
        if not accs:
            return {"empty": True}
        acc = accs[0]
        pf = load_for_account(acc)
        # merge live prices when possible
        prices = dict(self._prices)
        try:
            from prices import get_prices
            syms = [s.lower() for s, _, _ in MARKET]
            live = get_prices(syms, "auto") or {}
            for k, v in live.items():
                if v:
                    prices[k.upper()] = float(v)
                    self._prices[k.upper()] = float(v)
                    self._hist.setdefault(k.upper(), []).append((time.time(), float(v)))
        except Exception:
            pass
        # portfolio uses lowercase keys typically
        px_low = {k.lower(): v for k, v in prices.items()}
        equity = pf.total_equity(px_low)
        pnl = equity - float(pf.starting_balance or 0)
        holds = [("CASH", f"${pf.cash:,.2f}", "—")]
        for sym, pos in pf.positions.items():
            p = float(px_low.get(sym, 0) or 0)
            holds.append((sym.upper(), f"${pos.market_value(p):,.2f}", f"{pos.pnl(p):+.2f}"))
        return {
            "empty": False,
            "cash": pf.cash,
            "equity": equity,
            "pnl": pnl,
            "holds": holds,
            "mode": acc.mode,
            "bot": bool(self._bot_thread and self._bot_thread.is_alive()),
        }

    def _apply(self, data, err):
        self._busy = False
        if err:
            self.var_status.set("Error")
            self._log(err)
            return
        if not data or data.get("empty"):
            self.var_status.set("No account")
            return
        self.var_cash.set(f"${data['cash']:,.2f}")
        self.var_equity.set(f"${data['equity']:,.2f}")
        sign = "+" if data["pnl"] >= 0 else ""
        self.var_pnl.set(f"{sign}${data['pnl']:,.2f}")
        self.var_status.set(f"Paper · {data['mode']}")
        self.var_bot.set("BOT ON" if data.get("bot") else "BOT OFF")
        for i in self.tree.get_children():
            self.tree.delete(i)
        for row in data["holds"]:
            self.tree.insert("", tk.END, values=row)
        self._update_market_list()

    def do_trade(self, side: str):
        try:
            from accounts import enabled_accounts
            from portfolio import load_for_account, save_for_account, execute_buy, execute_sell
            accs = enabled_accounts()
            if not accs:
                messagebox.showerror("Trade", "No account")
                return
            acc = accs[0]
            pf = load_for_account(acc)
            sym = self._selected.lower()
            amt = float(str(self.trade_qty.get()).replace(",", "").strip() or "0")
            if amt <= 0:
                messagebox.showerror("Trade", "Enter a positive amount")
                return
            if side == "buy":
                ok, msg = execute_buy(pf, sym, amt, source="auto", note="exchange UI")
            else:
                # sell: if user enters 25/50/100 treat as percent of position
                if amt in (25, 50, 75, 100) or amt <= 100 and amt == int(amt):
                    ok, msg = execute_sell(pf, sym, f"{int(amt)}%", source="auto", note="exchange UI")
                else:
                    ok, msg = execute_sell(pf, sym, "all", source="auto", note="exchange UI")
            save_for_account(pf, acc)
            self._log(f"{side.upper()} {self._selected}: {msg}")
            self.refresh_async()
        except Exception as e:
            messagebox.showerror("Trade", str(e))
            self._log(f"Trade error: {e}")

    def start_bot(self):
        if self._bot_thread and self._bot_thread.is_alive():
            messagebox.showinfo("Bot", "Already running")
            return
        try:
            from config import STOP_FLAG, PID_FILE
            if STOP_FLAG.exists():
                STOP_FLAG.unlink(missing_ok=True)
            self._bot_stop.clear()

            def runner():
                try:
                    import auto_trader as at
                    _orig = at.should_stop
                    at.should_stop = lambda: self._bot_stop.is_set() or _orig()
                    try:
                        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
                    except Exception:
                        pass
                    self.after(0, lambda: self._log("Bot loop running…"))
                    at.run_loop()
                except Exception as e:
                    self.after(0, lambda: self._log(f"Bot error: {e}"))
                finally:
                    try:
                        from config import PID_FILE as P
                        P.unlink(missing_ok=True)
                    except Exception:
                        pass
                    self.after(0, lambda: self.var_bot.set("BOT OFF"))
                    self.after(0, lambda: self._log("Bot stopped"))

            self._bot_thread = threading.Thread(target=runner, daemon=True)
            self._bot_thread.start()
            self.var_bot.set("BOT ON")
            self._log("Bot started")
        except Exception as e:
            messagebox.showerror("Start", str(e))

    def stop_bot(self):
        self._bot_stop.set()
        try:
            from config import STOP_FLAG, PID_FILE
            STOP_FLAG.write_text("1", encoding="utf-8")
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        self.var_bot.set("BOT OFF")
        self._log("Stop requested")

    def open_settings(self):
        # reuse compact settings
        win = tk.Toplevel(self)
        win.title("Settings")
        win.configure(bg=C["bg"])
        win.geometry("420x520")
        canvas = tk.Canvas(win, bg=C["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        body = tk.Frame(canvas, bg=C["bg"])
        canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def wheel(e):
            d = int(-e.delta / 120) if getattr(e, "delta", 0) else (1 if getattr(e, "num", 0) == 5 else -1)
            canvas.yview_scroll(d, "units")

        win.bind("<Enter>", lambda e: (canvas.bind_all("<MouseWheel>", wheel), canvas.bind_all("<Button-4>", wheel), canvas.bind_all("<Button-5>", wheel)))
        win.bind("<Leave>", lambda e: (canvas.unbind_all("<MouseWheel>"), canvas.unbind_all("<Button-4>"), canvas.unbind_all("<Button-5>")))

        tk.Label(body, text="Paper capital (USD)", fg=C["muted"], bg=C["bg"]).pack(anchor=tk.W, padx=12, pady=(12, 0))
        cap = tk.StringVar(value="100")
        try:
            from accounts import enabled_accounts
            a = enabled_accounts()
            if a:
                cap.set(str(a[0].starting_balance))
        except Exception:
            pass
        tk.Entry(body, textvariable=cap, bg=C["card"], fg=C["text"], insertbackground=C["text"],
                 relief=tk.FLAT).pack(fill=tk.X, padx=12, pady=4)

        existing = {}
        for path in (ROOT / "keys" / "api_keys.env", ROOT / ".env"):
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.strip().startswith("#"):
                        k, _, v = line.partition("=")
                        existing[k.strip()] = v.strip()
        fields = {}
        for key, label in [
            ("FOMOAPI_KEY", "FOMO API key"),
            ("HELIUS_API_KEY", "Helius key"),
            ("RPC_HTTP_URL", "RPC URL"),
            ("GOPLUS_API_KEY", "GoPlus key"),
        ]:
            tk.Label(body, text=label, fg=C["muted"], bg=C["bg"]).pack(anchor=tk.W, padx=12, pady=(8, 0))
            var = tk.StringVar(value=existing.get(key, ""))
            tk.Entry(body, textvariable=var, bg=C["card"], fg=C["text"], insertbackground=C["text"],
                     relief=tk.FLAT, show="" if "URL" in key else "*").pack(fill=tk.X, padx=12, pady=2)
            fields[key] = var

        def save():
            try:
                amt = float(cap.get())
                from accounts import load_accounts, save_accounts
                from portfolio import Portfolio
                accs = load_accounts()
                for a in accs:
                    a.starting_balance = amt
                save_accounts(accs)
                for a in accs:
                    pf = Portfolio.load(a.state_file)
                    pf.starting_balance = amt
                    pf.cash = amt
                    pf.positions = {}
                    pf.trades = []
                    pf.save(a.state_file)
            except Exception as e:
                messagebox.showerror("Capital", str(e), parent=win)
                return
            (ROOT / "keys").mkdir(exist_ok=True)
            lines = ["# settings", ""]
            for k, var in fields.items():
                lines.append(f"{k}={var.get().strip()}")
            (ROOT / "keys" / "api_keys.env").write_text("\n".join(lines) + "\n", encoding="utf-8")
            messagebox.showinfo("Saved", "Settings saved", parent=win)
            win.destroy()
            self.refresh_async()

        tk.Label(body, text="  SAVE  ", fg=C["bg"], bg=C["green"], font=("Segoe UI", 10, "bold"),
                 cursor="hand2").pack(pady=16)
        body.winfo_children()[-1].bind("<Button-1>", lambda e: save())

    def _loop(self):
        if self._stop:
            return
        if not self._busy:
            self.refresh_async()
        self.after(8000, self._loop)

    def on_close(self):
        self._stop = True
        self._bot_stop.set()
        self.destroy()


def main():
    try:
        app = ExchangeApp()
        app.protocol("WM_DELETE_WINDOW", app.on_close)
        app.mainloop()
    except Exception:
        err = traceback.format_exc()
        try:
            r = tk.Tk(); r.withdraw()
            messagebox.showerror("Void Exchange failed", err[-1500:])
        except Exception:
            print(err)


if __name__ == "__main__":
    main()
