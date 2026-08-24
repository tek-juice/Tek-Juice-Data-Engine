#!/usr/bin/env bash
# DATA ENGINE — Database Migration Script
# Runs Alembic migrations against the configured database.
# Run from the project root: bash scripts/migrate.sh
#
# Options:
#   --rollback       Roll back the last migration
#   --revision DESC  Create a new auto-generated migration

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[MIGRATE]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}      $*"; }
error()   { echo -e "${RED}[ERROR]${NC}   $*" >&2; exit 1; }

# Load .env if present
if [[ -f ".env" ]]; then
    set -o allexport
    # shellcheck disable=SC1091
    source .env
    set +o allexport
fi

# Activate venv if present
if [[ -f ".venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Parse args
ROLLBACK=false
NEW_REVISION=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --rollback)  ROLLBACK=true; shift ;;
        --revision)  NEW_REVISION="$2"; shift 2 ;;
        *)           error "Unknown argument: $1" ;;
    esac
done

info "Verifying database connectivity..."
python3 -c "
import asyncio
import sys
from configs.database import check_db_health
ok = asyncio.run(check_db_health())
if not ok:
    print('Database unreachable — is PostgreSQL running?', file=sys.stderr)
    sys.exit(1)
print('Database connection OK.')
" || error "Cannot connect to database. Check POSTGRES_* vars in .env and that Docker is running."

if [[ "$ROLLBACK" == "true" ]]; then
    info "Rolling back last migration..."
    alembic downgrade -1
    success "Rolled back one migration."
    exit 0
fi

if [[ -n "$NEW_REVISION" ]]; then
    info "Creating new migration: $NEW_REVISION"
    alembic revision --autogenerate -m "$NEW_REVISION"
    success "Migration file created in database/migrations/versions/"
    exit 0
fi

info "Running pending migrations..."
alembic upgrade head

success "All migrations applied successfully."

# Run pgvector setup SQL
info "Ensuring PGVector extensions and indexes are configured..."
python3 -c "
import asyncio
from configs.database import AsyncSessionLocal
from sqlalchemy import text

async def run():
    async with AsyncSessionLocal() as session:
        await session.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        await session.execute(text('CREATE EXTENSION IF NOT EXISTS pg_trgm'))
        await session.execute(text('CREATE EXTENSION IF NOT EXISTS pgcrypto'))
        await session.commit()
        print('Extensions verified.')

asyncio.run(run())
"

success "Database migration complete."
