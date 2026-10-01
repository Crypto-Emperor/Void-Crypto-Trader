#!/usr/bin/env python3
"""
VoidTrade — Freqtrade-inspired paper trader (pure Python).
  python voidtrade.py trade      # start bot (dry-run)
  python voidtrade.py web        # open web UI
  python voidtrade.py status
  python voidtrade.py download-config
"""
from __future__ import annotations

import json
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # bundled source dir (read-only in exe)
if getattr(sys, "frozen", False):
    # Packaged .exe: user data (config/keys/state) lives next to the exe.
    DATA_ROOT = Path(sys.executable).resolve().parent
else:
    DATA_ROOT = ROOT
# Source modules first (so imports work from the bundle), then data dir.
for _p in (str(DATA_ROOT), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from dotenv import load_dotenv
    load_dotenv(DATA_ROOT / ".env")
    load_dotenv(DATA_ROOT / "keys" / "api_keys.env", override=True)
except Exception:
    pass


def load_config() -> dict:
    p = DATA_ROOT / "config_exchange.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"dry_run": True, "starting_balance": 100, "pairs": ["SOL", "BTC"]}


def cmd_status():
    from accounts import enabled_accounts
    from portfolio import load_for_account
    from prices import get_prices
    cfg = load_config()
    print("VoidTrade status")
    print(f"  dry_run: {cfg.get('dry_run', True)}")
    print(f"  strategy: {cfg.get('strategy')}")
    print(f"  pairs: {', '.join(cfg.get('pairs') or [])}")
    for acc in enabled_accounts():
        pf = load_for_account(acc)
        syms = [s.lower() for s in (cfg.get("pairs") or ["sol"])]
        try:
            prices = get_prices(syms, "auto") or {}
        except Exception:
            prices = {}
        eq = pf.total_equity(prices) if prices else pf.cash
        print(f"  account {acc.name}: cash ${pf.cash:,.2f} equity ~${eq:,.2f} positions={len(pf.positions)}")


def cmd_trade():
    cfg = load_config()
    print("Starting dry-run bot (Ctrl+C to stop)...")
    print(f"Strategy={cfg.get('strategy')} interval={cfg.get('check_interval_sec', 5)}s")
    import auto_trader
    auto_trader.run_loop()


def cmd_web():
    """Minimal pure-Python web UI (FreqUI-like)."""
    try:
        from flask import Flask, jsonify, request, Response
    except ImportError:
        print("Installing flask (one time)...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "flask"])
        from flask import Flask, jsonify, request, Response

    app = Flask(__name__)
    cfg = load_config()

    HTML = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/><title>VoidTrade</title>
<style>
body{margin:0;font-family:system-ui,Segoe UI,sans-serif;background:#0a0e14;color:#e7ecf3}
header{background:#0d1219;padding:14px 20px;border-bottom:1px solid #1e2a3a;display:flex;gap:16px;align-items:center}
h1{margin:0;font-size:18px;color:#59c2ff}
.badge{background:#1a2740;color:#8b9bb0;padding:4px 8px;border-radius:4px;font-size:12px}
main{display:grid;grid-template-columns:280px 1fr 300px;gap:12px;padding:12px;min-height:80vh}
.panel{background:#0d1219;border:1px solid #1e2a3a;border-radius:8px;padding:12px}
h2{margin:0 0 10px;font-size:12px;color:#8b9bb0;letter-spacing:.06em}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:8px 6px;border-bottom:1px solid #1e2a3a;text-align:left}
tr:hover{background:#121a26;cursor:pointer}
.green{color:#3dd68c}.red{color:#f07178}
.btn{background:#1a2740;color:#e7ecf3;border:1px solid #1e2a3a;padding:8px 12px;border-radius:6px;cursor:pointer;margin-right:6px}
.btn-go{background:#3dd68c;color:#0a0e14;border:none;font-weight:700}
.btn-stop{background:#f07178;color:#0a0e14;border:none;font-weight:700}
#log{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#8b9bb0;height:160px;overflow:auto;white-space:pre-wrap}
.kpi{font-size:20px;font-weight:700}
</style></head><body>
<header>
  <h1>VoidTrade</h1>
  <span class="badge">DRY-RUN / PAPER</span>
  <span class="badge" id="bot">BOT OFF</span>
  <button class="btn btn-go" onclick="startBot()">Start</button>
  <button class="btn btn-stop" onclick="stopBot()">Stop</button>
  <button class="btn" onclick="refresh()">Refresh</button>
</header>
<main>
  <div class="panel">
    <h2>MARKET</h2>
    <table id="mkt"><thead><tr><th>Pair</th><th>Price</th></tr></thead><tbody></tbody></table>
  </div>
  <div class="panel">
    <h2>ACCOUNT</h2>
    <div>Cash <div class="kpi" id="cash">—</div></div>
    <div style="margin-top:12px">Equity <div class="kpi" id="eq">—</div></div>
    <div style="margin-top:12px">PnL <div class="kpi" id="pnl">—</div></div>
    <h2 style="margin-top:20px">LOG</h2>
    <div id="log"></div>
  </div>
  <div class="panel">
    <h2>PORTFOLIO</h2>
    <table id="pf"><thead><tr><th>Asset</th><th>Value</th></tr></thead><tbody></tbody></table>
  </div>
</main>
<script>
async function refresh(){
  const r = await fetch('/api/status'); const j = await r.json();
  document.getElementById('cash').textContent = j.cash;
  document.getElementById('eq').textContent = j.equity;
  document.getElementById('pnl').textContent = j.pnl;
  document.getElementById('bot').textContent = j.bot ? 'BOT ON' : 'BOT OFF';
  document.getElementById('bot').style.color = j.bot ? '#3dd68c' : '#8b9bb0';
  let mt=''; (j.market||[]).forEach(x=>{mt+=`<tr><td>${x.sym}</td><td>${x.px}</td></tr>`;});
  document.querySelector('#mkt tbody').innerHTML=mt;
  let pt=''; (j.holds||[]).forEach(x=>{pt+=`<tr><td>${x[0]}</td><td>${x[1]}</td></tr>`;});
  document.querySelector('#pf tbody').innerHTML=pt;
  if(j.log) document.getElementById('log').textContent=j.log;
}
async function startBot(){ await fetch('/api/start',{method:'POST'}); refresh(); }
async function stopBot(){ await fetch('/api/stop',{method:'POST'}); refresh(); }
refresh(); setInterval(refresh, 4000);
</script></body></html>"""

    bot_thread = {"t": None, "stop": threading.Event()}

    @app.get("/")
    def index():
        return Response(HTML, mimetype="text/html")

    @app.get("/api/status")
    def api_status():
        from accounts import enabled_accounts
        from portfolio import load_for_account
        cfg = load_config()
        pairs = cfg.get("pairs") or ["SOL"]
        market = []
        holds = []
        cash = equity = pnl = 0.0
        logtxt = ""
        try:
            from prices import get_prices
            prices = get_prices([p.lower() for p in pairs], "auto") or {}
        except Exception:
            prices = {}
        demo = {"sol": 118.75, "btc": 95000, "eth": 3400, "bonk": 2e-5, "wif": 1.4, "jup": 0.8}
        for p in pairs:
            px = prices.get(p.lower()) or demo.get(p.lower()) or 1.0
            market.append({"sym": p, "px": f"${float(px):,.4g}"})
        try:
            accs = enabled_accounts()
            if accs:
                pf = load_for_account(accs[0])
                cash = pf.cash
                px_all = {**(demo), **{k: float(v) for k, v in prices.items()}}
                equity = pf.total_equity(px_all)
                pnl = equity - float(pf.starting_balance or 0)
                holds.append(["CASH", f"${cash:,.2f}"])
                for s, pos in pf.positions.items():
                    p = float(px_all.get(s, 0) or 0)
                    holds.append([s.upper(), f"${pos.market_value(p):,.2f}"])
        except Exception as e:
            logtxt = str(e)
        bot_on = bot_thread["t"] is not None and bot_thread["t"].is_alive()
        return jsonify({
            "cash": f"${cash:,.2f}",
            "equity": f"${equity:,.2f}",
            "pnl": f"${pnl:+,.2f}",
            "market": market,
            "holds": holds,
            "bot": bot_on,
            "log": logtxt or "VoidTrade dry-run. Pairs from config_exchange.json",
        })

    @app.post("/api/start")
    def api_start():
        if bot_thread["t"] and bot_thread["t"].is_alive():
            return jsonify({"ok": True, "msg": "already"})
        bot_thread["stop"].clear()
        try:
            from config import STOP_FLAG
            if STOP_FLAG.exists():
                STOP_FLAG.unlink(missing_ok=True)
        except Exception:
            pass

        def run():
            try:
                import auto_trader as at
                _o = at.should_stop
                at.should_stop = lambda: bot_thread["stop"].is_set() or _o()
                at.run_loop()
            except Exception:
                pass

        bot_thread["t"] = threading.Thread(target=run, daemon=True)
        bot_thread["t"].start()
        return jsonify({"ok": True})

    @app.post("/api/stop")
    def api_stop():
        bot_thread["stop"].set()
        try:
            from config import STOP_FLAG
            STOP_FLAG.write_text("1", encoding="utf-8")
        except Exception:
            pass
        return jsonify({"ok": True})

    print("VoidTrade web UI → http://127.0.0.1:8080")
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8080")).start()
    app.run(host="127.0.0.1", port=8080, debug=False, use_reloader=False)


def main():
    args = sys.argv[1:] or ["web"]
    cmd = args[0].lower()
    if cmd in ("status",):
        cmd_status()
    elif cmd in ("trade", "start"):
        cmd_trade()
    elif cmd in ("web", "ui"):
        cmd_web()
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:
        print(__doc__)
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
