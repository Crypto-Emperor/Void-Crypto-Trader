# Void Crypto Trader

Python crypto trading bot controlled from VS Code.

## Features

- **Simulation first** with real-time prices
- **Auto strategies**: top UP flow, pilot bias board, momentum, DCA, mean reversion
- **Free data layer** (`DATA_SOURCE=free`) — CoinPaprika + CoinGecko + DexScreener (no fomoapi credits)
- Optional **fomoapi.io** for real FOMO leader trades
- **1s on-chain wallet copy** (`wallet_copy.py`) via Solana RPC
- **Risk**: max **6% equity** / **75% cash** per trade
- Defensive sells on price drop / predicted drop
- Honeypot checks (GoPlus-style) before buys
- Multi-account (up to 5), quarterly projection, Oracle deploy scripts
- Live trade feed in the terminal when you run `start`

## Install (Windows / VS Code)

Open a terminal **in the `void-crypto-trader` folder**, then run these **one at a time**:

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

```powershell
.\.venv\Scripts\python.exe -m pip install rich click requests python-dotenv pydantic
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Do not rely on `Activate.ps1`** — Windows often blocks it. Always call the venv Python by full path as above.

### If `python` is not found

1. Install Python from https://www.python.org/downloads/
2. Check **“Add python.exe to PATH”**
3. Close and reopen the terminal
4. Try `py -m venv .venv` instead of `python -m venv .venv`
5. Then use:

```powershell
.\.venv\Scripts\python.exe -m pip install rich click requests python-dotenv pydantic
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Copy env (optional)

```powershell
copy .env.example .env
```


## API keys folder

All secrets go in the **`keys/`** folder (not in chat, not on GitHub).

```powershell
copy keys\api_keys.env.example keys\api_keys.env
```

Edit `keys\api_keys.env`. **Where to get keys:**

| Key | Get it here |
|-----|-------------|
| `FOMOAPI_KEY` | https://fomoapi.io/dashboard |
| `GOPLUS_API_KEY` | https://gopluslabs.io/ |
| `RPC_HTTP_URL` (Helius) | https://www.helius.dev/ |
| FOMO app account | https://fomo.family |

```env
FOMOAPI_KEY=
GOPLUS_API_KEY=
RPC_HTTP_URL=
SOLANA_PRIVATE_KEY=
```

The bot loads this file automatically. Full instructions: **`keys/README.md`**.

## Configure

Edit `.env` (defaults are safe sim):

| Setting | Meaning |
|---------|---------|
| `MODE=sim` | Paper trading |
| `DATA_SOURCE=free` | Free market data (no fomoapi payment) |
| `MAX_POSITION_PCT=6` | Max **6% of equity** per trade |
| Cash cap | **75%** of available cash (built-in) |
| `STRATEGY=top_accounts_copy` | Auto strategy (no manual follows) |
| `CHECK_INTERVAL_SEC=1` | How often the bot checks |

### Optional FOMO API

```env
DATA_SOURCE=fomoapi
FOMOAPI_KEY=your_key
CHECK_INTERVAL_SEC=600
```

### 1-second on-chain wallet copy

```env
TARGET_LEADER_WALLET=LeaderSolanaAddressHere
WALLET_COPY_MODE=sim
WALLET_COPY_INTERVAL_SEC=1
```

## Run commands

Always use the venv Python (works without activate):

```powershell
.\.venv\Scripts\python.exe bot.py status
.\.venv\Scripts\python.exe bot.py free-board
.\.venv\Scripts\python.exe bot.py who-up
.\.venv\Scripts\python.exe bot.py pilot
.\.venv\Scripts\python.exe bot.py predict
.\.venv\Scripts\python.exe bot.py start
.\.venv\Scripts\python.exe bot.py stop
.\.venv\Scripts\python.exe bot.py wallet-copy --test
.\.venv\Scripts\python.exe bot.py wallet-copy
```

| Command | Purpose |
|---------|---------|
| `status` | Portfolio snapshot (prints once, then exits — normal) |
| `start` | Auto-trader + **live trade feed** (Ctrl+C to stop) |
| `start --detach` | Background only, no live feed |
| `stop` | Stop auto-trader |
| `free-board` | Free market board |
| `who-up` | Leaders / free proxies |
| `pilot` | Bias / SL / TP board |
| `predict` | Short-horizon forecast |
| `wallet-copy` | 1s Solana leader copy |

### No venv (only if packages install globally)

```powershell
python -m pip install rich click requests python-dotenv pydantic
python bot.py status
python bot.py start
```

## Risk rules (built in)

| Rule | Value |
|------|--------|
| Max per trade | **6% of equity** |
| Max cash used | **75% of cash** |
| Honeypot check | On before new-token buys |
| Defensive sells | Momentum / 1h / 24h drop, predict sell, stop-loss |
| Default mode | Simulation |

## Not an .exe

Run with Python in the VS Code terminal so you can see logs and control start/stop.

## Disclaimer

Not financial advice. Crypto is high risk. Default is paper trading. Live trading can lose money.
