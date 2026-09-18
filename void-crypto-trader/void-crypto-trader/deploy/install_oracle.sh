#!/usr/bin/env bash
# Install FOMO sim bot on Oracle Cloud Always Free (Ubuntu ARM or x86)
# Run as the linux user that will own the bot (e.g. ubuntu / opc)
set -euo pipefail

BOT_DIR="${BOT_DIR:-$HOME/fomo-sim-bot}"
PYTHON="${PYTHON:-python3}"

echo "==> Installing system packages"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo apt-get install -y python3 python3-pip python3-venv git curl
elif command -v yum >/dev/null 2>&1; then
  sudo yum install -y python3 python3-pip git curl
fi

echo "==> Bot directory: $BOT_DIR"
mkdir -p "$BOT_DIR"
# If this script is inside the repo, copy; else assume already present
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -f "$REPO_ROOT/bot.py" ]]; then
  rsync -a --exclude '.venv' --exclude '__pycache__' --exclude 'portfolio_state.json' \
    --exclude 'autotrader.pid' --exclude 'STOP' --exclude 'autotrader.log' \
    "$REPO_ROOT/" "$BOT_DIR/"
fi

cd "$BOT_DIR"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env — edit STARTING_BALANCE, STRATEGY, HONEYPOT_CHECK, etc."
fi

echo "==> Python venv + deps"
$PYTHON -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

echo "==> Optional: Playwright browsers (only if you enable live FOMO UI)"
# playwright install-deps chromium || true
# playwright install chromium || true

echo "==> Installing systemd user service (survives reboot)"
mkdir -p "$HOME/.config/systemd/user"
SERVICE_FILE="$HOME/.config/systemd/user/fomo-bot.service"
cat > "$SERVICE_FILE" << UNIT
[Unit]
Description=FOMO Sim Auto-Trader
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/.venv/bin/python $BOT_DIR/auto_trader.py
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1
# Load .env via WorkingDirectory; dotenv in app also loads it

[Install]
WantedBy=default.target
UNIT

# Enable lingering so user services run without login
if command -v loginctl >/dev/null 2>&1; then
  sudo loginctl enable-linger "$(whoami)" || true
fi

systemctl --user daemon-reload
systemctl --user enable fomo-bot.service

echo ""
echo "Done."
echo "  Edit config:  nano $BOT_DIR/.env"
echo "  Start bot:    systemctl --user start fomo-bot"
echo "  Status:       systemctl --user status fomo-bot"
echo "  Logs:         journalctl --user -u fomo-bot -f"
echo "  Or use CLI:   cd $BOT_DIR && .venv/bin/python bot.py status"
echo ""
echo "Oracle tip: use Always Free Ampere A1 (2 OCPU / 12 GB max as of mid-2026)"
echo "  Shape: VM.Standard.A1.Flex — keep within free quota to avoid reclaim."
