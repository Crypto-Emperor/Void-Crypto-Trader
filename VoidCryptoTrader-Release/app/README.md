# Void Crypto Trader

## Desktop GUI (charts + controls)

```powershell
python -m pip install matplotlib
python gui_app.py
```

**GUI features**
- Portfolio equity graph
- Coin / memecoin price graph (pick symbol)
- Start / Stop bot
- Set capital, reset portfolio
- Holdings table + activity log

### Build Windows .exe

```powershell
build_exe.bat
```

Or:

```powershell
python -m pip install pyinstaller matplotlib
python -m PyInstaller --noconfirm --windowed --name VoidCryptoTrader gui_app.py
```

Run `dist\VoidCryptoTrader.exe` from the project folder (so `keys\`, `.env`, and portfolio files work).

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
| `HELIUS_API_KEY` / RPC | https://www.helius.dev/ |
| `QUICKNODE_RPC_URL` | https://www.quicknode.com/ |
| `ALCHEMY_API_KEY` | https://www.alchemy.com/ |
| `CHAINSTACK_RPC_URL` | https://chainstack.com/ |
| `TRITON_RPC_URL` | https://triton.one/ |
| `RPC_HTTP_URL` (any paid) | paste full HTTPS endpoint |
| FOMO app account | https://fomo.family |

```env
FOMOAPI_KEY=
GOPLUS_API_KEY=
RPC_HTTP_URL=
SOLANA_PRIVATE_KEY=
```

The bot loads this file automatically. Run `python bot.py providers` to see which RPC is active.

Full instructions: **`keys/README.md**`.

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

## Trading phases

### Phase 1 — Sim / paper (default)
- `MODE=sim` and `TRADING_PHASE=sim`
- Mock portfolio; theoretical fills logged with **latency in ms**
- Journal file: `trade_journal.csv` (signal -> fill timing)
- Compare fills to live market prices while you learn

### Phase 2 — Micro-live (optional)
- Keep the same **percent** rules (6% equity / 75% cash)
- Use a small account (e.g. $100 -> ~$6 max per idea)
- Optional: `MICRO_LIVE=true` and `MICRO_LIVE_SCALE=1.0` (or lower)
- Tests real fees and API speed without large capital risk

### Multiple coins at once
- One tick can **PLAN/FILL several buys and sells**
- Defaults: up to **8 buys per tick**, up to **15 open positions**
- Env: `COPY_MAX_BUYS_PER_TICK`, `COPY_MAX_POSITIONS`

## Automatic position sizing

The bot **chooses how much to buy** for you (`COPY_SIZE_MODE=smart`):

- Uses a share of your equity (about 3-6% per trade idea)
- Sizes up a little when more leaders agree or rank is higher
- Sizes down on small accounts (e.g. $100)
- Hard caps: **max 6% of equity** and **75% of free cash**
- You do **not** need to pick dollar amounts each trade

## Understanding the live feed

When you run `python bot.py start`, lines mean:

| Message | Meaning |
|---------|---------|
| `Bot started` | Auto-trader is on |
| `Watching... no trade this tick` | Checked markets; nothing to do yet |
| `PLAN BUY / PLAN SELL` | About to trade (or try to) |
| `FILLED BUY / FILLED SELL` | Trade completed in sim (or live) |
| `BUY BLOCKED / SELL BLOCKED` | Skipped (risk limit, honeypot, no cash, etc.) |
| `Protect: ... sell` | Selling because price looks weak |
| `Outlook:` | Short prediction hint |
| `Bot stopped` | Shut down |

`status` only prints a snapshot once, then exits. That is normal.

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


## FOMO memecoins

Requires `FOMOAPI_KEY` from https://fomoapi.io/dashboard for real FOMO token boards.
`python bot.py fomo-tokens` lists them.


## License

Apache License 2.0 — see **LICENSE** file.

Paper trading / education tool. Not financial advice. Live trading is at your own risk.
