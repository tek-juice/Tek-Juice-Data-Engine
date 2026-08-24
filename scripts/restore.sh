#!/usr/bin/env bash
# DATA ENGINE — Restore Script
# Restores a PostgreSQL database and vector storage from a backup archive.
#
# Usage:
#   bash scripts/restore.sh --file storage/exports/backup_20250711_120000.tar.gz
#   bash scripts/restore.sh --file storage/exports/backup_20250711_120000.tar.gz --skip-vectors

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}[RESTORE]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}      $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}   $*"; }
error()   { echo -e "${RED}[ERROR]${NC}   $*" >&2; exit 1; }

# Load .env
if [[ -f ".env" ]]; then
    set -o allexport
    # shellcheck disable=SC1091
    source .env
    set +o allexport
fi

BACKUP_FILE=""
SKIP_VECTORS=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --file)         BACKUP_FILE="$2"; shift 2 ;;
        --skip-vectors) SKIP_VECTORS=true; shift ;;
        *)              error "Unknown argument: $1" ;;
    esac
done

[[ -z "$BACKUP_FILE" ]] && error "Usage: bash scripts/restore.sh --file <backup.tar.gz>"
[[ ! -f "$BACKUP_FILE" ]] && error "Backup file not found: $BACKUP_FILE"

echo -e "\n${BOLD}${RED}⚠  WARNING: This will OVERWRITE the current database.${NC}"
echo -e "Backup file: ${BOLD}${BACKUP_FILE}${NC}"
read -rp "Type 'yes' to confirm: " CONFIRM
[[ "$CONFIRM" != "yes" ]] && { echo "Restore cancelled."; exit 0; }

# ── Extract archive ───────────────────────────────────────────────────────────
EXTRACT_DIR="storage/exports/.restore_tmp_$(date +%s)"
mkdir -p "$EXTRACT_DIR"
info "Extracting backup archive..."
tar -xzf "$BACKUP_FILE" -C "$EXTRACT_DIR" --strip-components=1
success "Archive extracted."

# Locate files
DB_DUMP=$(find "$EXTRACT_DIR" -name "database.pgdump" | head -1)
VECTORS_ARCHIVE=$(find "$EXTRACT_DIR" -name "vectors.tar.gz" | head -1)
[[ -z "$DB_DUMP" ]] && error "database.pgdump not found in backup archive."

# ── Drop & recreate database ──────────────────────────────────────────────────
info "Dropping and recreating database: ${POSTGRES_DB:-data_engine}..."
PGPASSWORD="${POSTGRES_PASSWORD:-}" psql \
    -h "${POSTGRES_HOST:-localhost}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER:-data_engine_user}" \
    -d "postgres" \
    -c "DROP DATABASE IF EXISTS ${POSTGRES_DB:-data_engine};" \
    -c "CREATE DATABASE ${POSTGRES_DB:-data_engine};" \
    2>/dev/null || error "Failed to recreate database."
success "Database recreated."

# ── Restore database ──────────────────────────────────────────────────────────
info "Restoring database from dump..."
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_restore \
    -h "${POSTGRES_HOST:-localhost}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER:-data_engine_user}" \
    -d "${POSTGRES_DB:-data_engine}" \
    --no-password \
    --no-owner \
    --exit-on-error \
    "$DB_DUMP" || error "pg_restore failed."
success "Database restored."

# ── Restore vectors ───────────────────────────────────────────────────────────
if [[ "$SKIP_VECTORS" == "false" && -n "$VECTORS_ARCHIVE" ]]; then
    info "Restoring vector storage..."
    rm -rf storage/vectors/*
    tar -xzf "$VECTORS_ARCHIVE" -C storage/ 2>/dev/null || warn "Vector restore failed."
    success "Vector storage restored."
else
    info "Skipping vector storage restore."
fi

# ── Run any new migrations ────────────────────────────────────────────────────
info "Applying any pending migrations..."
if [[ -f ".venv/bin/activate" ]]; then source .venv/bin/activate; fi
alembic upgrade head
success "Migrations applied."

# ── Cleanup ───────────────────────────────────────────────────────────────────
rm -rf "$EXTRACT_DIR"
echo -e "\n${GREEN}${BOLD}Restore complete.${NC}\n"
