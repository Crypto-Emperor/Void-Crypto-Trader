# Run on Oracle Cloud Always Free

## 1. Create the free VM

1. Sign up: https://www.oracle.com/cloud/free/
2. Compute → Create instance
3. Image: **Canonical Ubuntu 22.04** or 24.04 (ARM)
4. Shape: **VM.Standard.A1.Flex** (Ampere ARM)
5. Stay within Always Free limits (as of mid-2026 roughly **2 OCPU + 12 GB RAM** total across A1)
6. Add your SSH public key
7. Create VCN with a public subnet + assign public IP
8. Security list: allow SSH (22) from your IP

Capacity is often “out of host capacity” in busy regions — retry or try another AD/region.

## 2. SSH in and install the bot

```bash
ssh ubuntu@YOUR_PUBLIC_IP   # or opc@ on Oracle Linux

# Upload the project (from your laptop)
# scp -r fomo-sim-bot ubuntu@IP:~/

cd ~/fomo-sim-bot
bash deploy/install_oracle.sh
```

Or clone if you push the repo to git.

## 3. Configure

```bash
nano ~/fomo-sim-bot/.env
```

Important flags:
```
MODE=sim
STARTING_BALANCE=10000
HONEYPOT_CHECK=true
HONEYPOT_STRICT=true
STRATEGY=momentum
CHECK_INTERVAL_SEC=60
SYMBOLS=sol,eth,btc
```

## 4. Run 24/7

```bash
systemctl --user start fomo-bot
systemctl --user status fomo-bot
journalctl --user -u fomo-bot -f
```

Stop:
```bash
systemctl --user stop fomo-bot
```

CLI from the server:
```bash
cd ~/fomo-sim-bot
source .venv/bin/activate
python bot.py status
python bot.py project
python bot.py stop    # if you started via bot.py start instead of systemd
```

## 5. Keep the free tier

- Do not exceed Always Free shape quotas
- Idle reclaim: Oracle may terminate long-idle free instances — keep the bot active or use minimal traffic
- Snapshot boot volume if you care about state backups

## 6. Honeypot checks on the server

Same as local: every buy of non-allow-listed tokens hits GoPlus before order.
Optional: register at https://gopluslabs.io for higher rate limits and set `GOPLUS_API_KEY` in `.env`.
