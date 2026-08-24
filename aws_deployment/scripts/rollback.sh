#!/bin/bash
# ============================================================
# Tek Juice Data Engine — Rollback Script
# Rolls back to the previous Docker image version
# Usage: bash rollback.sh <image_tag>
# Example: bash rollback.sh sha-a1b2c3d
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

log()   { echo -e "${GREEN}[ROLLBACK]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/aws_deployment/docker/docker-compose.yml"
APP_PORT=9600

TARGET_TAG="${1:-}"
if [ -z "${TARGET_TAG}" ]; then
    error "Usage: bash rollback.sh <image_tag>\nExample: bash rollback.sh sha-a1b2c3d"
fi

log "Rolling back to image tag: ${TARGET_TAG}"

# Update the image tag in compose and redeploy
export IMAGE_TAG="${TARGET_TAG}"
docker compose -f "${COMPOSE_FILE}" up -d --no-build app

sleep 10
curl -sf "http://localhost:${APP_PORT}/health" || error "Rollback health check failed."

log "Rollback to ${TARGET_TAG} successful."
