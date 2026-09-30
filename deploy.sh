#!/usr/bin/env bash
set -euo pipefail

# Hermes Dashboard — Deployment Script
# Run from your LOCAL machine (not the server)
# Usage: ./deploy.sh [secret-code]

SERVER="${DEPLOY_SERVER:-root@YOUR_SERVER_IP}"
REMOTE_DIR="/root/hermes-dashboard"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SECRET="${1:-}"

echo "=== Hermes Dashboard Deployment ==="
echo ""

# 1. Upload files
echo "[1/7] Uploading dashboard files..."
ssh "$SERVER" "mkdir -p $REMOTE_DIR"
rsync -avz --exclude='venv' --exclude='*.db' --exclude='.env' --exclude='__pycache__' \
    "$SCRIPT_DIR/" "$SERVER:$REMOTE_DIR/"

# 2. Set up Python venv + deps
echo "[2/7] Installing Python dependencies..."
ssh "$SERVER" "cd $REMOTE_DIR && python3.11 -m venv venv && venv/bin/pip install -q -r requirements.txt"

# 3. Create .env if it doesn't exist
if [ -n "$SECRET" ]; then
    echo "[3/7] Configuring dashboard secret..."
    ssh "$SERVER" "cat > $REMOTE_DIR/.env << EOF
DASHBOARD_SECRET=$SECRET
HERMES_HOME=/root/.hermes
DASHBOARD_DB=$REMOTE_DIR/dashboard.db
ALERT_DAILY_USD=25
ALERT_WEEKLY_USD=100
ALERT_MONTHLY_USD=300
SESSION_TTL_DAYS=7
API_BASE=http://localhost:3333
EOF"
else
    echo "[3/7] Checking .env..."
    ssh "$SERVER" "test -f $REMOTE_DIR/.env || (echo 'ERROR: No .env file and no secret provided. Run: ./deploy.sh YOUR_SECRET'; exit 1)"
fi

# 4. Install Caddy (if not already installed)
echo "[4/7] Ensuring Caddy is installed..."
ssh "$SERVER" 'command -v caddy >/dev/null 2>&1 || {
    apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
    curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/gpg.key" | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf "https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt" | tee /etc/apt/sources.list.d/caddy-stable.list
    apt update -y
    apt install -y caddy
}'

# 5. Configure Caddy
echo "[5/7] Configuring Caddy + firewall..."
ssh "$SERVER" "cp $REMOTE_DIR/Caddyfile /etc/caddy/Caddyfile && mkdir -p /var/log/caddy"
ssh "$SERVER" "ufw allow 80/tcp >/dev/null 2>&1; ufw allow 443/tcp >/dev/null 2>&1; true"

# 6. Install + start systemd service
echo "[6/7] Installing systemd service..."
ssh "$SERVER" "cp $REMOTE_DIR/hermes-dashboard.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now hermes-dashboard && systemctl restart hermes-dashboard"
ssh "$SERVER" "systemctl restart caddy"

# 7. Configure Hermes to use proxy
echo "[7/7] Configuring Hermes API proxy..."
ssh "$SERVER" 'grep -q "ANTHROPIC_BASE_URL" ~/.hermes/.env 2>/dev/null && sed -i "s|ANTHROPIC_BASE_URL=.*|ANTHROPIC_BASE_URL=http://localhost:3333|" ~/.hermes/.env || echo "ANTHROPIC_BASE_URL=http://localhost:3333" >> ~/.hermes/.env'
ssh "$SERVER" "systemctl restart hermes-gateway"

echo ""
echo "=== Deployment complete! ==="
echo ""
echo "Dashboard:  https://dashboard.YOUR_DOMAIN.com"
echo "Secret:     ${SECRET:-<check .env on server>}"
echo ""
echo "IMPORTANT: Make sure DNS A record exists:"
echo "  dashboard.YOUR_DOMAIN.com -> YOUR_SERVER_IP"
echo ""
echo "Check status:"
echo "  ssh $SERVER 'systemctl status hermes-dashboard'"
echo "  ssh $SERVER 'systemctl status caddy'"
echo "  ssh $SERVER 'systemctl status hermes-gateway'"
