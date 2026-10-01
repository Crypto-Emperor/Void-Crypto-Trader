# Void Crypto Trader

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/license-Apache--2.0-green?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/mode-paper%20first-orange?style=flat-square" alt="Paper first">
  <img src="https://img.shields.io/badge/chain-Solana%20%C2%B7%20ETH%20%C2%B7%20CEX-lightgrey?style=flat-square" alt="Chains">
  <img src="https://img.shields.io/badge/strategy-freqtrade--inspired-8A2BE2?style=flat-square" alt="Freqtrade inspired">
</p>

A crypto trading bot with a **web dashboard**, a **terminal desk**, and **desktop charts** —
inspired by [Freqtrade](https://github.com/freqtrade/freqtrade), written in pure Python.
Free market data, memecoin support, honeypot protection, risk caps, Phantom-wallet
copy-trading, and optional live execution through Jupiter.

---

## ⚡ One launcher. Zero fuss.

**Windows:** double-click [`Void.bat`](Void.bat). **Linux / macOS:** run [`./Void.sh`](Void.sh). That's the whole setup.

It finds (or installs) Python, pulls dependencies once, and opens the dashboard at
`http://127.0.0.1:8080`. No PATH spelunking, no five scripts to keep in sync.

```bat
Void.bat            :: web dashboard (default)
Void.bat trade      :: terminal trading desk
Void.bat status     :: portfolio snapshot
Void.bat gui        :: desktop chart terminal
Void.bat menu       :: interactive picker
Void.bat build      :: compile a real VoidCryptoTrader.exe (PyInstaller)
```

Same commands on Linux / macOS:

```sh
./Void.sh           # web dashboard (default)
./Void.sh trade     # terminal trading desk
./Void.sh status    # portfolio snapshot
./Void.sh gui       # desktop chart terminal
./Void.sh menu      # interactive picker
./Void.sh build     # compile a single-file executable for your OS
```

> Prefer an `.exe`? Run `Void.bat build` once on Windows and you get a single-file
> `VoidCryptoTrader.exe` that runs without Python installed.

---

## ✨ Highlights

| | |
|---|---|
| 🛡️ **Paper-first** | Dry-run by default; live trading is opt-in with daily loss caps and circuit breakers |
| 📊 **Real indicators** | RSI, ATR, MACD, EMA confluence strategy with trailing stops & protections (freqtrade-style) |
| 🐸 **Memecoins** | GeckoTerminal / DexScreener OHLCV for DEX-only tokens, degen-score ranking, dedicated meme sleeve (2% size, 10% total cap) |
| 🕵️ **Wallet copy** | 1-second Solana wallet watcher + Phantom wallet integration via Jupiter swaps |
| 🧪 **Honeypot checks** | GoPlus security scan before any meme buy — if sells aren't allowed, no entry |
| 🔑 **Keys isolated** | All secrets live in `keys/api_keys.env`, gitignored, never in code |

## 🗂️ Layout

```
.
├── Void.bat                     ← Windows launcher (the only thing you touch)
├── Void.sh                      ← Linux / macOS launcher (same commands)
├── void-crypto-trader/          ← source (bot, strategies, risk, executor)
│   └── void-crypto-trader/
│       ├── bot.py               terminal trading desk
│       ├── market_data.py       CEX + DEX candles
│       ├── strategies/          confluence, momentum, dca, copy...
│       ├── executor/            Jupiter live execution
│       └── keys/                api_keys.env.example → copy me
└── VoidCryptoTrader-Release/    ← packaged app used by Void.bat
    └── app/
        ├── voidtrade.py         web dashboard server
        ├── gui_app.py           desktop charts
        └── void_launcher.py     single-exe entry point
```

## 🔧 Quick start (manual)

Don't want the launcher? Three commands (works on Windows, Linux and macOS):

```bash
cd VoidCryptoTrader-Release/app
pip install -r requirements.txt
python voidtrade.py web
```

Configure pairs, capital and dry-run in `app/config_exchange.json`; API keys in
`app/keys/api_keys.env` (copy `api_keys.env.example`).

## 🚨 Live trading checklist

1. Start with `LIVE_DRY_RUN=true` and watch fills for a few days.
2. Fund the Phantom key with **only what you can lose** (memecoins move fast).
3. Keep `LIVE_TRADING=false` until your paper equity curve looks sane.
4. Set `DAILY_LOSS_CAP` — the engine halts itself when hit. No exceptions.

## ⚠️ Disclaimer

Educational software. Crypto trading is extremely risky; most memecoins go to zero.
Nothing here is financial advice. You are responsible for every transaction you sign.

## 📜 License

Apache-2.0 — see [LICENSE](LICENSE).
