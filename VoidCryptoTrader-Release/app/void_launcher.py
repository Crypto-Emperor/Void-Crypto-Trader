#!/usr/bin/env python3
"""
Void Crypto Trader - single-exe launcher.

Double-click VoidCryptoTrader.exe and pick what you want:
  * Terminal Trading Desk (rich CLI, bot.py)
  * Web dashboard (flask, voidtrade.py web)
  * Desktop chart terminal (Tkinter, gui_app.py)
  * Auto-trader start / stop / status helpers

Everything runs INSIDE the exe - no Python install needed by the user.
The auto-trader worker is spawned as a detached child of this same exe
(hidden flag "--mode worker"), which re-invokes itself instead of python.

Data (.env, keys/, *.json state, logs) lives NEXT TO the exe, exactly like
the old folder layout, so existing portfolios keep working.

CLI usage (also works directly on the exe):
  VoidCryptoTrader.exe                        -> GUI menu
  VoidCryptoTrader.exe status                 -> run bot.py command
  VoidCryptoTrader.exe buy sol 100            -> manual trade
  VoidCryptoTrader.exe --mode cli status      -> internal hidden mode
  VoidCryptoTrader.exe --mode web             -> flask dashboard
  VoidCryptoTrader.exe --mode gui             -> tkinter charts
  VoidCryptoTrader.exe --mode worker          -> auto_trader loop (internal)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# ------------------------------------------------------------------ paths --
def app_dir() -> Path:
    """Folder that contains the .exe (or this file when running from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = app_dir()


def _setup_env() -> None:
    """cwd + sys.path so bundled modules import and data files are found."""
    try:
        os.chdir(ROOT)
    except Exception:
        pass
    # Bundled sources live in _MEIPASS; user data (keys/, *.json) next to exe.
    for p in (str(ROOT), str(getattr(sys, "_MEIPASS", ROOT))):
        if p not in sys.path:
            sys.path.insert(0, p)


# ------------------------------------------------------- hidden entrypoints --
def run_cli(argv: list[str]) -> int:
    """Run the rich CLI (bot.py click group) inside this process."""
    _setup_env()
    from bot import cli  # click group
    try:
        cli(args=argv, standalone_mode=False)
    except SystemExit as e:
        return int(e.code or 0)
    except Exception as e:
        print(f"[Void] CLI error: {e}")
        import traceback
        traceback.print_exc()
    return 0


def run_web(argv: list[str]) -> int:
    """Run the flask web dashboard (voidtrade.py)."""
    _setup_env()
    from voidtrade import cmd_web
    try:
        cmd_web()
    except KeyboardInterrupt:
        pass
    return 0


def run_gui(argv: list[str]) -> int:
    """Run the Tkinter exchange-style chart app (gui_app.py)."""
    _setup_env()
    import gui_app
    gui_app.main()
    return 0


def run_worker(argv: list[str]) -> int:
    """Run the background auto-trader loop (auto_trader.py __main__)."""
    _setup_env()
    import runpy
    runpy.run_module("auto_trader", run_name="__main__")
    return 0


MODES = {"cli": run_cli, "web": run_web, "gui": run_gui, "worker": run_worker}


# ------------------------------------------------------------- GUI menu ----
CHOICES = [
    ("Terminal Trading Desk", "cli", [],
     "Interactive bot: buy/sell, portfolio, projections, copy-trade."),
    ("Web Dashboard", "web", [],
     "Browser UI at http://127.0.0.1:8080."),
    ("Desktop Chart Terminal", "gui", [],
     "Exchange-style screen with live price charts."),
    ("Start Auto-Trader", "start", ["--mode", "worker"],
     "Runs strategies in the background while you sleep."),
    ("Stop Auto-Trader", "stop", ["cli", "stop"],
     "Writes STOP flag + ends the worker process."),
    ("Portfolio Status", "status", ["cli", "status"],
     "Quick snapshot of equity, positions and PnL."),
]


def _child_cmd(extra: list[str]) -> list[str]:
    """Command that re-launches THIS exe (or this script) with args."""
    if getattr(sys, "frozen", False):
        return [sys.executable] + extra
    src = str(Path(__file__).resolve())
    return [sys.executable, src] + extra


def _popen_hidden(cmd: list[str]) -> None:
    kwargs: dict = {"stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def _console_window(extra: list[str]) -> None:
    """Open a VISIBLE console window running the exe/script with args."""
    cmd = _child_cmd(extra)
    if os.name == "nt":
        joined = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        subprocess.Popen(f'start "Void Crypto Trader" {joined}', shell=True)
        return
    # Linux/macOS dev fallback: try a terminal emulator, else run detached
    for term in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm"):
        try:
            subprocess.Popen([term, "--"] + cmd, start_new_session=True)
            return
        except FileNotFoundError:
            continue
    _popen_hidden(cmd)


def build_menu(root) -> None:
    import tkinter as tk
    from tkinter import ttk

    root.title("Void Crypto Trader")
    root.geometry("580x450")
    root.configure(bg="#0b0e14")

    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True, padx=18, pady=16)

    tk.Label(frame, text="VOID CRYPTO TRADER",
             font=("Consolas", 17, "bold"), bg="#0b0e14",
             fg="#7ee787").pack(anchor="w")
    tk.Label(frame, text=f"data folder: {ROOT}",
             font=("Consolas", 9), bg="#0b0e14",
             fg="#8b949e").pack(anchor="w", pady=(0, 10))

    def launch(extra: list[str], visible: bool = True) -> None:
        try:
            if visible:
                _console_window(extra)
            else:
                _popen_hidden(_child_cmd(extra))
        except Exception as e:
            tk.messagebox.showerror("Void", f"Could not launch: {e}")

    for label, _mode, extra, desc in CHOICES:
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=3)
        tk.Button(row, text=label, width=26, anchor="w",
                  command=lambda x=extra: launch(x),
                  bg="#161b22", fg="#e6edf3", relief="flat",
                  activebackground="#21262d", activeforeground="#7ee787",
                  font=("Consolas", 10)).pack(side="left")
        tk.Label(row, text=desc, bg="#0b0e14", fg="#8b949e",
                 font=("Consolas", 8), wraplength=280,
                 justify="left").pack(side="left", padx=10)

    tk.Button(frame, text="Quit", command=root.destroy,
              bg="#da3633", fg="white", relief="flat",
              font=("Consolas", 10, "bold")).pack(side="bottom", fill="x",
                                                  pady=(12, 0))


def main_menu() -> int:
    import tkinter as tk
    import tkinter.messagebox  # noqa: F401  (used by launch error path)
    root = tk.Tk()
    build_menu(root)
    root.mainloop()
    return 0


# ------------------------------------------------------------------- main --
KNOWN_CLI = {"status", "start", "stop", "buy", "sell", "reset",
             "set-capital", "project", "accounts", "copy", "scan",
             "wallet", "predict", "tokens", "leaderboard", "version",
             "help", "--help", "-h"}


def main() -> int:
    argv = sys.argv[1:]

    # Internal hidden modes used by spawned children.
    if argv and argv[0] == "--mode":
        mode = argv[1] if len(argv) > 1 else ""
        fn = MODES.get(mode)
        if fn:
            return fn(argv[2:])
        print(f"Unknown mode '{mode}'. Choices: {', '.join(MODES)}")
        return 2

    # Friendly passthrough: VoidCryptoTrader.exe status / buy sol 100 ...
    if argv:
        head = argv[0].lower()
        if head == "web":
            return run_web(argv[1:])
        if head == "gui":
            return run_gui(argv[1:])
        if head in KNOWN_CLI:
            return run_cli(argv)

    # No usable args -> GUI menu.
    return main_menu()


if __name__ == "__main__":
    sys.exit(main())
