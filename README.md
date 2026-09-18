# Void Crypto Trader

Python crypto trading bot you control from **VS Code**.

Simulation-first auto trading with free market data (CoinPaprika, CoinGecko, DexScreener), optional FOMO API leader flows, defensive sells, honeypot checks, and optional 1-second on-chain Solana wallet copy.

## Highlights

- **Paper trading by default** (`MODE=sim`)
- **Auto strategies** (top-flow copy, pilot board, momentum, DCA, …)
- **Risk caps**: Max 6% equity / 75% cash per trade
- **Live trade feed** in your VS Code terminal
- **API keys isolated** securely in `keys/`
- **Optional FOMO API + Solana RPC** for live execution paths

---

## Getting Started

### 1. Prerequisites
Ensure you have the following installed on your local machine:
- **Python 3.10+**
- **VS Code** (with the Python extension installed)
- **Git** (optional, for cloning)

### 2. Installation
Clone this repository or download the source code zip file, then navigate to the project directory:

```bash
cd void-crypto-trader
```

Create a virtual environment and install the required dependencies:

```bash
# Create virtual environment
python -m venv venv

# Activate it (Windows)
.\venv\Scripts\activate

# Activate it (Mac/Linux)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration & Security

Void Crypto Trader isolates all sensitive credentials. 

1. Create a `keys/` directory in the root folder if it doesn't exist.
2. Inside `keys/`, create a `.env` file or `config.json` using the provided template:

```env
# Core Settings
MODE=sim  # Options: sim, live

# API Credentials (Optional for basic market data, required for live/Solana)
SOLANA_RPC_URL=your_secure_rpc_endpoint
FOMO_API_KEY=your_fomo_api_key
```

**Security Note:** The `keys/` directory is automatically added to `.gitignore` to prevent you from accidentally pushing your private keys or RPC endpoints to GitHub. *Never share your secrets.*

---

## Usage

To start the bot, simply open the repository folder in VS Code, open the integrated terminal (`Ctrl + \``), and run:

```bash
python main.py
```

### Selecting a Strategy
When you run the bot, you will be prompted to choose a strategy, or you can pass it via the command line:

```bash
python main.py --strategy momentum
```

Available strategies include:
- `sim-copy`: Simulates copying top-flow wallets.
- `momentum`: Scans DexScreener/CoinGecko for rapid volume changes.
- `dca`: Dollar-cost averaging based on preset risk caps.

---

## Risk Management & Safety

This bot is hardcoded with strict safety guardrails to protect capital:
- **Max Loss Stop:** Automatic defensive sells if a position drops below your risk threshold.
- **Honeypot Protection:** Pre-trade checks to ensure smart contracts allow selling before capital is committed.
- **Exposure Cap:** Restricts individual trades to a maximum of 6% of total equity.

---

## License

This project is licensed under the Apache-2.0 License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

*Void Crypto Trader is for educational and experimental purposes only. Cryptocurrency trading carries a high level of risk. The creators are not responsible for any financial losses incurred while using this software, whether in simulation or live modes.*
