#!/bin/bash
# ============================================================
# Tek Juice Data Engine — Deploy Script
# Run on the EC2 server to start/update the application
# Usage: bash deploy.sh [--pull]
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()   { echo -e "${GREEN}[DEPLOY]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_PORT=9600
COMPOSE_FILE="${PROJECT_ROOT}/aws_deployment/docker/docker-compose.yml"

# ── Validate environment file ──────────────────────────────────
if [ ! -f "${PROJECT_ROOT}/.env" ]; then
    error ".env file not found at ${PROJECT_ROOT}/.env\nRun: cp .env.example .env && nano .env"
fi

log "Starting deployment of Tek Juice Data Engine on port ${APP_PORT}..."

# ── Pull latest images if --pull flag passed ───────────────────
if [[ "${1:-}" == "--pull" ]]; then
    log "Pulling latest Docker images..."
    docker compose -f "${COMPOSE_FILE}" pull
fi

# ── Build images ───────────────────────────────────────────────
log "Building application image..."
docker compose -f "${COMPOSE_FILE}" build --no-cache app

# ── Start / update services ────────────────────────────────────
log "Starting services..."
docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans

# ── Wait for app to be healthy ─────────────────────────────────
log "Waiting for app to become healthy on port ${APP_PORT}..."
MAX_RETRIES=12
COUNT=0
until curl -sf "http://localhost:${APP_PORT}/health" > /dev/null 2>&1; do
    COUNT=$((COUNT + 1))
    if [ "${COUNT}" -ge "${MAX_RETRIES}" ]; then
        error "App did not become healthy after $((MAX_RETRIES * 5)) seconds."
    fi
    warn "Waiting... (${COUNT}/${MAX_RETRIES})"
    sleep 5
done

# ── Clean up old images ────────────────────────────────────────
log "Cleaning up unused Docker images..."
docker image prune -f

# ── Summary ───────────────────────────────────────────────────
echo ""
echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN}  Deployment successful!${NC}"
echo -e "${GREEN}=====================================================${NC}"
echo ""
echo "  App URL : http://$(curl -s ifconfig.me):${APP_PORT}"
echo ""
docker compose -f "${COMPOSE_FILE}" ps
