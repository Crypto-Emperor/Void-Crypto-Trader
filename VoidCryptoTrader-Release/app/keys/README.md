# API keys & providers

Paste free or **paid** keys here. The bot picks the first available Solana RPC.

## Solana RPC (needed for fast 0.1s wallet copy)

| Provider | Signup | What to put in `api_keys.env` |
|----------|--------|-------------------------------|
| **Helius** | https://www.helius.dev/ | `HELIUS_API_KEY=...` or full `HELIUS_RPC_URL=` |
| **QuickNode** | https://www.quicknode.com/ | `QUICKNODE_RPC_URL=https://...` (full endpoint) |
| **Alchemy** | https://www.alchemy.com/ | `ALCHEMY_API_KEY=...` |
| **Chainstack** | https://chainstack.com/ | `CHAINSTACK_RPC_URL=https://...` |
| **Triton** | https://triton.one/ | `TRITON_RPC_URL=https://...` (paid) |
| **Any paid RPC** | your dashboard | `RPC_HTTP_URL=https://...` (always works) |

**Priority:** `RPC_HTTP_URL` -> Helius -> QuickNode -> Alchemy -> Chainstack -> Triton -> public Solana.

Public RPC is free but **not** reliable at 0.1s. For 0.1s use Helius/Alchemy/Chainstack free or paid.

## Other APIs

| Key | Signup |
|-----|--------|
| `FOMOAPI_KEY` | https://fomoapi.io/dashboard |
| `GOPLUS_API_KEY` | https://gopluslabs.io/ |
| `JUPITER_API_KEY` | https://station.jup.ag/ |

## Check what is active

```powershell
python bot.py providers
```

## Paid later

1. Create account on Helius / QuickNode / etc.
2. Paste key or URL into `keys/api_keys.env`
3. Restart the bot — no code changes

Never commit `api_keys.env` or private keys.
