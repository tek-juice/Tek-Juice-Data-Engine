#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# DATA ENGINE — One-shot database setup
# Runs all 6 SQL migrations then seeds the first tenant + admin user.
#
# Usage (from DATA_ENGINE/ directory):
#   bash setup_db.sh
#
# Prerequisites: docker compose stack must be running
#   docker compose up -d postgres pgbouncer redis
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DB_CONTAINER="data_engine_postgres"
DB_USER="data_engine_user"
DB_NAME="data_engine"

# ── Colour helpers ─────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓  $*${NC}"; }
info() { echo -e "${YELLOW}  ▸  $*${NC}"; }
err()  { echo -e "${RED}  ✗  $*${NC}"; exit 1; }

echo ""
echo "  ⚡  DATA ENGINE — Database Setup"
echo "  ─────────────────────────────────────────────────"

# ── 1. Wait for Postgres to be healthy ────────────────────────────────────
info "Waiting for Postgres to be ready…"
for i in $(seq 1 30); do
    if docker exec "$DB_CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" -q 2>/dev/null; then
        ok "Postgres is ready"
        break
    fi
    [ $i -eq 30 ] && err "Postgres not ready after 30s. Run: docker compose up -d postgres"
    sleep 1
done

# ── 2. Run migrations in order ────────────────────────────────────────────
SCHEMAS=(
    "001_initial_schema.sql"
    "002_telemetry_schema.sql"
    "003_seo_geo_schema.sql"
    "004_rls_policies.sql"
    "005_aeo_schema.sql"
    "006_rank_authority_schema.sql"
)

info "Running schema migrations…"
for f in "${SCHEMAS[@]}"; do
    info "  Applying $f …"
    docker exec -i "$DB_CONTAINER" \
        psql -U "$DB_USER" -d "$DB_NAME" \
        < "database/schemas/$f" \
        && ok "  $f" \
        || err "  $f FAILED — check the output above"
done

# ── 3. Seed first tenant + admin user ─────────────────────────────────────
info "Seeding admin tenant and user…"

# Read credentials from .env
ADMIN_EMAIL=$(grep "^ADMIN_EMAIL=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' || echo "")
ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' || echo "")

# Fallback defaults if not set in .env
ADMIN_EMAIL="${ADMIN_EMAIL:-nobert.ndungutse@tekjuice.co.uk}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-TekJuice@Admin2026!}"

# Hash the password using the same bcrypt path that configs/security.py uses at runtime.
# Using the gateway container (always running) via configs.security.hash_password.
HASHED=$(docker exec data_engine_gateway python -c "
from configs.security import hash_password
print(hash_password('${ADMIN_PASSWORD}'))
" 2>/dev/null || echo "")

if [ -z "$HASHED" ]; then
    err "Could not hash password — make sure data_engine_gateway container is running"
fi

docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" <<SQL
DO \$\$
DECLARE
    v_tenant_id UUID;
    v_user_id   UUID;
BEGIN
    -- Insert tenant (idempotent)
    INSERT INTO tenants (name, slug, tier)
    VALUES ('Tek Juice', 'tek-juice', 'enterprise')
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_tenant_id;

    -- Fetch existing if already seeded
    IF v_tenant_id IS NULL THEN
        SELECT id INTO v_tenant_id FROM tenants WHERE slug = 'tek-juice';
        RAISE NOTICE 'Tenant already exists: %', v_tenant_id;
    ELSE
        RAISE NOTICE 'Created tenant: %', v_tenant_id;
    END IF;

    -- Insert admin user (idempotent)
    INSERT INTO users (tenant_id, email, hashed_password, full_name, role)
    VALUES (v_tenant_id, '${ADMIN_EMAIL}', '${HASHED}', 'Admin', 'admin')
    ON CONFLICT (email) DO NOTHING
    RETURNING id INTO v_user_id;

    IF v_user_id IS NULL THEN
        RAISE NOTICE 'User already exists: ${ADMIN_EMAIL}';
    ELSE
        RAISE NOTICE 'Created admin user: ${ADMIN_EMAIL} (id: %)', v_user_id;
    END IF;
END;
\$\$;
SQL

ok "Tenant and admin user ready"

# ── 4. Summary ─────────────────────────────────────────────────────────────
echo ""
echo "  ─────────────────────────────────────────────────"
ok "Database setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Get a JWT token:"
echo "     curl -s -X POST http://localhost:8000/api/v1/auth/token \\"
echo "       -d 'username=${ADMIN_EMAIL}&password=${ADMIN_PASSWORD}'"
echo ""
echo "  2. Create an API key (use the access_token from step 1):"
echo "     curl -s -X POST http://localhost:8000/api/v1/auth/api-keys \\"
echo "       -H 'Authorization: Bearer <access_token>' \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"name\": \"personal\"}'"
echo ""
echo "  3. Use the api_key in all subsequent requests:"
echo "     curl -H 'X-API-Key: <your_key>' http://localhost:8000/api/v1/..."
echo ""
