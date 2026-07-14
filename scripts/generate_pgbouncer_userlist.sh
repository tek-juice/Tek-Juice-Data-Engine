#!/usr/bin/env bash
# DATA ENGINE — Generate PgBouncer userlist.txt
# Reads POSTGRES_PASSWORD and POSTGRES_USER from .env and writes
# the MD5-hashed entry that PgBouncer requires.
#
# Usage:
#   chmod +x scripts/generate_pgbouncer_userlist.sh
#   ./scripts/generate_pgbouncer_userlist.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$ROOT_DIR/.env"
USERLIST="$ROOT_DIR/infrastructure/pgbouncer/userlist.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env file not found at $ENV_FILE"
  exit 1
fi

# Source only the DB variables we need
POSTGRES_USER=$(grep "^POSTGRES_USER=" "$ENV_FILE" | cut -d'=' -f2 | tr -d '"')
POSTGRES_PASSWORD=$(grep "^POSTGRES_PASSWORD=" "$ENV_FILE" | cut -d'=' -f2 | tr -d '"')

if [[ -z "$POSTGRES_USER" || -z "$POSTGRES_PASSWORD" ]]; then
  echo "ERROR: POSTGRES_USER or POSTGRES_PASSWORD not set in .env"
  exit 1
fi

# PgBouncer MD5 format: md5(password + username)
HASH=$(echo -n "${POSTGRES_PASSWORD}${POSTGRES_USER}" | md5sum | awk '{print $1}')
ADMIN_HASH=$(echo -n "pgbouncer_admin_secure${POSTGRES_USER}" | md5sum | awk '{print $1}')

cat > "$USERLIST" << EOF
# DATA ENGINE — PgBouncer User List (auto-generated)
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# DO NOT COMMIT THIS FILE — it contains hashed credentials
"${POSTGRES_USER}" "md5${HASH}"
"pgbouncer_admin" "md5${ADMIN_HASH}"
EOF

echo "✓ PgBouncer userlist.txt generated at $USERLIST"
echo "  User: $POSTGRES_USER"
echo "  Hash: md5${HASH:0:8}..."
