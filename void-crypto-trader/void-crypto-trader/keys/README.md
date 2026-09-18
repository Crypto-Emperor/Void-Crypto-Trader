# API keys (keep private)

Put your secrets in this folder. **Do not share or upload these files.**

## Where to get each key

| Key | What it is for | Get it here | Cost |
|-----|----------------|-------------|------|
| **FOMOAPI_KEY** | Real FOMO leaderboard / trader trades | https://fomoapi.io/dashboard | Free tier available |
| **GOPLUS_API_KEY** | Honeypot / token security checks | https://gopluslabs.io/ | Free tier available |
| **RPC_HTTP_URL** | Faster Solana RPC (wallet copy) | https://www.helius.dev/ or https://www.quicknode.com/ | Free tier available |
| **SOLANA_PRIVATE_KEY** | Live on-chain trades only | Your wallet (export private key) | N/A - **never share** |
| **FOMO_EMAIL / FOMO_PASSWORD** | Live FOMO app login (advanced) | https://fomo.family | Your account |

### FOMO API (optional)

1. Open **https://fomoapi.io/dashboard**
2. Sign in with email code (no password)
3. Copy your API key
4. Paste into `api_keys.env` as `FOMOAPI_KEY=...`

Docs: https://fomoapi.io/docs  
Pricing: https://fomoapi.io/pricing  

You can run the bot **without** this key using free market data (`DATA_SOURCE=free`).

### GoPlus (optional)

1. Open **https://gopluslabs.io/**
2. Create an account / get an API key from their developer docs
3. Paste as `GOPLUS_API_KEY=...`

Without it, honeypot checks use public endpoints where possible, or skip soft-fail.

### Solana RPC (optional)

Public RPC works for testing. For 1-second wallet copy, a free Helius key is better:

1. **https://www.helius.dev/** → sign up → create API key  
2. Set:

```env
RPC_HTTP_URL=https://mainnet.helius-rpc.com/?api-key=YOUR_KEY
```

### Solana private key (live only)

Only if you enable **live** wallet copy. Export from your wallet app, store only in `api_keys.env`, never in chat or GitHub.

---

## Quick setup

```powershell
copy keys\api_keys.env.example keys\api_keys.env
```

Edit `keys\api_keys.env`:

```env
FOMOAPI_KEY=
GOPLUS_API_KEY=
RPC_HTTP_URL=
SOLANA_PRIVATE_KEY=
```

The bot loads this file automatically on start.

## Safety

- Never commit `api_keys.env` to GitHub
- Never paste private keys in Discord/chat
- Leave `SOLANA_PRIVATE_KEY` empty for simulation
