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

**Works on Windows, Linux and macOS.** Runs paper-trading out of the box with free data —
no API keys needed to try it.

---

## Setup (30 seconds)

**Requirements:** nothing but an internet connection. The launcher finds Python for you
(and prints the right install command for your OS if it's missing).

### Windows

1. Download / clone this repo.
2. Double-click **[`Void.bat`](Void.bat)**.

### Linux / macOS

```sh
git clone https://github.com/YOURNAME/void-crypto-trader.git
cd void-crypto-trader
chmod +x Void.sh      # only needed once after cloning
./Void.sh
```

On first run the launcher installs dependencies automatically (about a minute, one time
only), then opens the web dashboard at **`http://127.0.0.1:8080`**. That's it — no PATH
spelunking, no virtualenv wrangling, no five scripts to keep in sync.

> Prefer an executable? Run `Void.bat build` on Windows for a single-file
> `VoidCryptoTrader.exe`, or `./Void.sh build` on Linux/macOS for a standalone binary —
> both work without Python installed.

## Commands

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

## Highlights

| | |
|---|---|
| **Paper-first** | Dry-run by default; live trading is opt-in with daily loss caps and circuit breakers |
| **Real indicators** | RSI, ATR, MACD, EMA confluence strategy with trailing stops & protections (freqtrade-style) |
| **Memecoins** | GeckoTerminal / DexScreener OHLCV for DEX-only tokens, degen-score ranking, dedicated meme sleeve (2% size, 10% total cap) |
| **Wallet copy** | 1-second Solana wallet watcher + Phantom wallet integration via Jupiter swaps |
| **Honeypot checks** | GoPlus security scan before any meme buy — if sells aren't allowed, no entry |
| **Keys isolated** | All secrets live in `keys/api_keys.env`, gitignored, never in code |

## Configuration & API keys

**Paper trading needs zero keys** — market data comes from free public APIs
(Binance/Coinbase candles, GeckoTerminal/DexScreener for memecoins).

Optional keys go in **`VoidCryptoTrader-Release/app/keys/api_keys.env`**:

```sh
cd VoidCryptoTrader-Release/app/keys
cp api_keys.env.example api_keys.env    # then edit with any text editor
```

| Variable | What it unlocks | Where to get it |
|---|---|---|
| `GOPLUS_API_KEY` | Honeypot / sell-tax scans before meme buys | [gopluslabs.io](https://gopluslabs.io/) |
| `FOMOAPI_KEY` | Real FOMO trending / most-held boards | [fomoapi.io](https://fomoapi.io/dashboard) |
| `RPC_HTTP_URL` or `HELIUS_API_KEY` | Fast Solana RPC for wallet copy-trading | [helius.dev](https://www.helius.dev/) |
| `JUPITER_API_KEY` | Higher-rate Jupiter swap quotes | [station.jup.ag](https://station.jup.ag/) |
| `SOLANA_PRIVATE_KEY` *(live only)* | Signing real swaps via Phantom-exported key | your wallet |

The file is **gitignored** — your secrets never leave your machine. Pairs, capital and
dry-run mode live in `app/config_exchange.json`.

To go live later: set `LIVE_TRADING=true`, keep `LIVE_DRY_RUN=true` for the first few
days, and always set `LIVE_DAILY_LOSS_LIMIT_USD` (see checklist below).

## Layout

```
.
├── Void.bat                     <- Windows launcher (the only thing you touch)
├── Void.sh                      <- Linux / macOS launcher (same commands)
├── void-crypto-trader/          <- source (bot, strategies, risk, executor)
│   └── void-crypto-trader/
│       ├── bot.py               terminal trading desk
│       ├── market_data.py       CEX + DEX candles
│       ├── strategies/          confluence, momentum, dca, copy...
│       ├── executor/            Jupiter live execution
│       └── keys/                api_keys.env.example -> copy me
└── VoidCryptoTrader-Release/    <- packaged app used by Void.bat
    └── app/
        ├── voidtrade.py         web dashboard server
        ├── gui_app.py           desktop charts
        └── void_launcher.py     single-exe entry point
```

## Quick start (manual, without the launcher)

Three commands — works identically on Windows, Linux and macOS:

```bash
cd VoidCryptoTrader-Release/app
pip install -r requirements.txt
python voidtrade.py web          # Windows: py -3 voidtrade.py web
```

Then open **http://127.0.0.1:8080**. Other sub-commands: `voidtrade.py trade`,
`voidtrade.py status`, `voidtrade.py gui`.

## Live trading checklist

1. Start with `LIVE_DRY_RUN=true` and watch fills for a few days.
2. Fund the Phantom key with **only what you can lose** (memecoins move fast).
3. Keep `LIVE_TRADING=false` until your paper equity curve looks sane.
4. Set `DAILY_LOSS_CAP` — the engine halts itself when hit. No exceptions.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `./Void.sh: Permission denied` | Run `chmod +x Void.sh` once (common after copying files between machines) |
| Launcher says Python not found | Install Python 3.10+ ([python.org](https://www.python.org/downloads/)); on Ubuntu: `sudo apt-get install -y python3 python3-pip` |
| First-run dependency install fails | Re-run the launcher; if pip keeps failing, use the manual quick start above with a venv |
| Dashboard won't open in browser | Go to `http://127.0.0.1:8080` manually; check nothing else uses port 8080 |
| `gui` does nothing on a server | Headless machine — no display for Tkinter; use `web` or `trade` instead |
| Live swaps rejected / honeypot warnings | Normal safety rails — that's the GoPlus scan doing its job |

## Disclaimer

Educational software. Crypto trading is extremely risky; most memecoins go to zero.
Nothing here is financial advice. You are responsible for every transaction you sign.

## License

Apache-2.0 — see [LICENSE](LICENSE).
