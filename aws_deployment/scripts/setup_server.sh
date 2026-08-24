#!/bin/bash
# ============================================================
# Tek Juice Data Engine — Server Setup Script
# Run this ONCE on a fresh EC2 instance (Ubuntu 22.04)
# Usage: bash setup_server.sh
# ============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log()    { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()   { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

APP_DIR="/opt/tek-juice-data-engine"
APP_PORT=9600

log "Starting server setup for Tek Juice Data Engine..."

# ── 1. System Updates ──────────────────────────────────────────
log "Updating system packages..."
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install -y curl git unzip ufw htop

# ── 2. Install Docker ──────────────────────────────────────────
log "Installing Docker..."
if command -v docker &> /dev/null; then
    warn "Docker already installed. Skipping."
else
    sudo apt-get install -y ca-certificates gnupg lsb-release
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo usermod -aG docker "$USER"
    sudo systemctl enable docker
    sudo systemctl start docker
    log "Docker installed successfully."
fi

# ── 3. Configure Firewall ──────────────────────────────────────
log "Configuring UFW firewall..."
sudo ufw --force reset
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp    comment 'SSH'
sudo ufw allow 80/tcp    comment 'HTTP'
sudo ufw allow 443/tcp   comment 'HTTPS'
sudo ufw allow ${APP_PORT}/tcp comment 'Tek Juice App'
sudo ufw --force enable
log "Firewall configured. Port ${APP_PORT} is open."

# ── 4. Create App Directory ────────────────────────────────────
log "Creating app directory at ${APP_DIR}..."
sudo mkdir -p "${APP_DIR}"
sudo chown "$USER":"$USER" "${APP_DIR}"

# ── 5. Create Systemd Service ──────────────────────────────────
log "Creating systemd service for auto-start..."
sudo tee /etc/systemd/system/tek-juice.service > /dev/null <<EOF
[Unit]
Description=Tek Juice Data Engine
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${APP_DIR}
ExecStart=/usr/bin/docker compose -f ${APP_DIR}/aws_deployment/docker/docker-compose.yml up -d
ExecStop=/usr/bin/docker compose -f ${APP_DIR}/aws_deployment/docker/docker-compose.yml down
TimeoutStartSec=300
User=$USER

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable tek-juice.service
log "Systemd service created and enabled."

# ── 6. Summary ─────────────────────────────────────────────────
echo ""
echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN}  Server setup complete!${NC}"
echo -e "${GREEN}=====================================================${NC}"
echo ""
echo "  App port  : ${APP_PORT}"
echo "  App dir   : ${APP_DIR}"
echo ""
echo "  Next steps:"
echo "  1. Upload your project files to ${APP_DIR}"
echo "  2. Create your .env file: cp .env.example .env && nano .env"
echo "  3. Run: bash ${APP_DIR}/aws_deployment/scripts/deploy.sh"
echo ""
warn "NOTE: Log out and back in for Docker group permissions to take effect."
