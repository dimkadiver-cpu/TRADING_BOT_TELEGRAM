#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/trading_bot"
VENV_DIR="/opt/trading_bot/.venv"
SERVICE_NAME="trading-bot"
REPO_URL="https://github.com/dimkadiver-cpu/TRADING_BOT_TELEGRAM.git"
BRANCH="main"

echo "==> Stop service"
systemctl stop "$SERVICE_NAME" || true
pkill -f "$APP_DIR/main.py" 2>/dev/null || true

echo "==> Preserve server-specific files"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Preserve .env
[ -f "$APP_DIR/.env" ] && cp -a "$APP_DIR/.env" "$TMP_DIR/.env"

# Preserve only selected config files
mkdir -p "$TMP_DIR/config"

[ -f "$APP_DIR/config/channels.yaml" ] && \
  cp -a "$APP_DIR/config/channels.yaml" "$TMP_DIR/config/channels.yaml"

[ -f "$APP_DIR/config/telegram_control.yaml" ] && \
  cp -a "$APP_DIR/config/telegram_control.yaml" "$TMP_DIR/config/telegram_control.yaml"

cd "$APP_DIR"

echo "==> Ensure git repo"
if [ ! -d ".git" ]; then
  git init
  git remote add origin "$REPO_URL"
else
  git remote set-url origin "$REPO_URL"
fi

echo "==> Configure sparse checkout"
git sparse-checkout init --no-cone || true

cat > .git/info/sparse-checkout <<'EOF'
/main_linux_server.py
/requirements.txt
/README.md
/.gitignore
/src/
/config/
/db/migrations/
/db/ops_migrations/
/scripts/
EOF

echo "==> Pull latest code"
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
echo "==> Remove generated main.py before pull"
rm -f "$APP_DIR/main.py"

echo "==> Pull latest code"
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
git sparse-checkout reapply || true

echo "==> Restore server-specific files"

# Restore .env
[ -f "$TMP_DIR/.env" ] && cp -a "$TMP_DIR/.env" "$APP_DIR/.env"

# Restore only selected config files
mkdir -p "$APP_DIR/config"

[ -f "$TMP_DIR/config/channels.yaml" ] && \
  cp -a "$TMP_DIR/config/channels.yaml" "$APP_DIR/config/channels.yaml"

[ -f "$TMP_DIR/config/telegram_control.yaml" ] && \
  cp -a "$TMP_DIR/config/telegram_control.yaml" "$APP_DIR/config/telegram_control.yaml"

echo "==> Apply Linux main"
cp -f "$APP_DIR/main_linux_server.py" "$APP_DIR/main.py"

cmp -s "$APP_DIR/main_linux_server.py" "$APP_DIR/main.py" \
  && echo "OK: main.py = main_linux_server.py" \
  || { echo "ERROR: main.py differs from main_linux_server.py"; exit 1; }

echo "==> Install/update requirements"
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Remove runtime locks"
rm -f "$APP_DIR/.bot.lock" "$APP_DIR/.telesignalbot.lock"

echo "==> Start service"
systemctl start "$SERVICE_NAME"

echo "==> Service status"
systemctl status "$SERVICE_NAME" --no-pager --lines=30

echo "==> Recent logs"
journalctl -u "$SERVICE_NAME" -n 60 --no-pager

