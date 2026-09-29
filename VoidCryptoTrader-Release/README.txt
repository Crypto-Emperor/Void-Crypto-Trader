VOIDTRADE (Freqtrade-style, pure Python)
========================================
License: Apache 2.0

QUICK START (seconds, not minutes)
----------------------------------
1. Install Python 3 once (python.org, Add to PATH)
2. Double-click:  VoidTrade.bat
3. Browser opens http://127.0.0.1:8080

CLI (from app folder)
---------------------
python voidtrade.py web       Web UI (recommended)
python voidtrade.py trade     Bot only in terminal
python voidtrade.py status    Portfolio snapshot

Config: app/config_exchange.json  (pairs, dry_run, strategy)
Keys:   app/keys/api_keys.env

ABOUT .EXE
----------
A real Windows .exe must be built on YOUR PC:
  BUILD EXE (run once on Windows).bat
First package install is one-time; later starts are fast.

This is inspired by Freqtrade's dry-run + UI idea, written
only in Python (not a fork of Freqtrade).
