#!/usr/bin/env bash
# DATA ENGINE — Backup Script
# Creates a timestamped backup of the PostgreSQL database and vector storage.
# Backups are written to storage/exports/
#
# Usage: bash scripts/backup.sh [--upload-s3]

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${CYAN}[BACKUP]${NC}  $*"; }
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

UPLOAD_S3=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --upload-s3) UPLOAD_S3=true; shift ;;
        *) error "Unknown argument: $1" ;;
    esac
done

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="storage/exports/backup_${TIMESTAMP}"
mkdir -p "$BACKUP_DIR"

info "Starting backup — timestamp: $TIMESTAMP"

# ── PostgreSQL dump ───────────────────────────────────────────────────────────
info "Dumping PostgreSQL database: ${POSTGRES_DB:-data_engine}..."
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
    -h "${POSTGRES_HOST:-localhost}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER:-data_engine_user}" \
    -d "${POSTGRES_DB:-data_engine}" \
    --no-password \
    --format=custom \
    --compress=9 \
    --file="${BACKUP_DIR}/database.pgdump" \
    2>/dev/null || error "pg_dump failed. Is PostgreSQL running and accessible?"

DB_SIZE=$(du -sh "${BACKUP_DIR}/database.pgdump" | cut -f1)
success "Database backup complete — size: $DB_SIZE"

# ── Vector storage ────────────────────────────────────────────────────────────
if [[ -d "storage/vectors" && "$(ls -A storage/vectors 2>/dev/null)" ]]; then
    info "Archiving vector storage..."
    tar -czf "${BACKUP_DIR}/vectors.tar.gz" -C storage vectors/ 2>/dev/null || \
        warn "Vector archive failed — directory may be empty."
    VECTOR_SIZE=$(du -sh "${BACKUP_DIR}/vectors.tar.gz" 2>/dev/null | cut -f1 || echo "0")
    success "Vector storage archived — size: $VECTOR_SIZE"
else
    info "No vector files to archive — skipping."
fi

# ── Manifest ──────────────────────────────────────────────────────────────────
cat > "${BACKUP_DIR}/manifest.json" << EOF
{
  "timestamp":        "${TIMESTAMP}",
  "backup_dir":       "${BACKUP_DIR}",
  "postgres_host":    "${POSTGRES_HOST:-localhost}",
  "postgres_db":      "${POSTGRES_DB:-data_engine}",
  "database_backup":  "database.pgdump",
  "vectors_backup":   "vectors.tar.gz",
  "created_at":       "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
success "Manifest written."

# ── Tar the whole backup dir ──────────────────────────────────────────────────
ARCHIVE="storage/exports/backup_${TIMESTAMP}.tar.gz"
tar -czf "$ARCHIVE" -C storage/exports "backup_${TIMESTAMP}/"
rm -rf "$BACKUP_DIR"
TOTAL_SIZE=$(du -sh "$ARCHIVE" | cut -f1)
success "Backup archive: $ARCHIVE (size: $TOTAL_SIZE)"

# ── Optional S3 upload ────────────────────────────────────────────────────────
if [[ "$UPLOAD_S3" == "true" ]]; then
    if command -v aws >/dev/null 2>&1 && [[ -n "${S3_BACKUP_BUCKET:-}" ]]; then
        info "Uploading backup to S3: s3://${S3_BACKUP_BUCKET}/backups/"
        aws s3 cp "$ARCHIVE" "s3://${S3_BACKUP_BUCKET}/backups/$(basename "$ARCHIVE")"
        success "Uploaded to S3."
    else
        warn "--upload-s3 flag set but aws CLI or S3_BACKUP_BUCKET not configured."
    fi
fi

# ── Retention: delete backups older than 30 days ─────────────────────────────
info "Cleaning up backups older than 30 days..."
find storage/exports/ -name "backup_*.tar.gz" -mtime +30 -delete 2>/dev/null || true
success "Retention policy applied."

echo -e "\n${GREEN}Backup complete: ${ARCHIVE}${NC}\n"
