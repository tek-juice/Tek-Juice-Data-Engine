# DATA ENGINE — Deployment Guide

---

## Phase 1: Local Development

### Prerequisites
- Python 3.11+
- Docker Desktop
- PostgreSQL 16 with PGVector (or use Docker)
- Redis

### Setup

```bash
# Clone repository
git clone <repo-url>
cd DATA_ENGINE

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys and settings

# Start infrastructure services
docker-compose up -d postgres redis

# Run migrations
make migrate

# Seed initial data
make seed

# Start all services locally
docker-compose up -d
```

### Verify Installation
```bash
make health
```

---

## Phase 2: Docker Compose (Full Stack)

Run the complete platform locally using Docker Compose:

```bash
# Build all images
docker-compose build

# Start all services
docker-compose up -d

# Check logs
docker-compose logs -f

# Check individual service
docker-compose logs -f ingestion_service

# Scale a service
docker-compose up -d --scale celery_worker=4
```

### Service Ports (local)
| Service | URL |
|---------|-----|
| API Gateway | http://localhost:8000 |
| Ingestion | http://localhost:8001 |
| Embedding | http://localhost:8003 |
| Vector Vault | http://localhost:8004 |
| Telemetry | http://localhost:8005 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

---

## Phase 3: Kubernetes (Staging / Production)

### Prerequisites
- kubectl configured
- Helm 3+
- Container registry access

### Deploy to Kubernetes

```bash
# Create namespace
kubectl create namespace data-engine

# Apply secrets
kubectl apply -f infrastructure/kubernetes/secrets/ -n data-engine

# Apply ConfigMaps
kubectl apply -f infrastructure/kubernetes/configmaps/ -n data-engine

# Deploy PostgreSQL
kubectl apply -f infrastructure/kubernetes/postgres/ -n data-engine

# Deploy Redis
kubectl apply -f infrastructure/kubernetes/redis/ -n data-engine

# Deploy services
kubectl apply -f infrastructure/kubernetes/services/ -n data-engine

# Deploy ingress
kubectl apply -f infrastructure/kubernetes/ingress/ -n data-engine

# Verify pods
kubectl get pods -n data-engine
```

### Rolling Update
```bash
# Update a specific service image
kubectl set image deployment/ingestion-service \
  ingestion-service=registry/data-engine/ingestion:v2.0.0 \
  -n data-engine

# Monitor rollout
kubectl rollout status deployment/ingestion-service -n data-engine

# Rollback if needed
kubectl rollout undo deployment/ingestion-service -n data-engine
```

---

## Phase 4: Cloud Production (Terraform)

### Node Architecture

**Node 1 — Compute-Optimised Server**
- Hosts: Python APIs, admin services, telemetry services, processing workers
- Recommended: c6i.2xlarge (AWS) or equivalent
- Storage: 100GB SSD

**Node 2 — Memory-Optimised Database Server**
- Hosts: PostgreSQL + PGVector, HNSW indexing
- Recommended: r6i.2xlarge (AWS) or equivalent
- Storage: 500GB+ NVMe SSD

### Terraform Deployment

```bash
cd infrastructure/terraform

# Initialise
terraform init

# Plan
terraform plan -var-file=production.tfvars

# Apply
terraform apply -var-file=production.tfvars

# Destroy (caution!)
terraform destroy -var-file=production.tfvars
```

### Environment Variables for Production

Sensitive values are managed via cloud secrets manager (AWS Secrets Manager / GCP Secret Manager). Never store production secrets in `.env` files.

```bash
# Export secrets to environment (example for AWS)
export POSTGRES_PASSWORD=$(aws secretsmanager get-secret-value \
  --secret-id data-engine/postgres-password \
  --query SecretString --output text)
```

---

## Database Migrations

Run migrations before deploying any new version:

```bash
# Run all pending migrations
make migrate

# Or directly
bash scripts/migrate.sh

# Check migration status
alembic current

# Create a new migration
alembic revision --autogenerate -m "description"

# Rollback one step
alembic downgrade -1
```

---

## Backup and Restore

### Backup
```bash
# Full backup (database + vectors + exports)
make backup

# Or directly
bash scripts/backup.sh
```

Backups are written to `storage/exports/` by default and can be uploaded to cloud storage.

### Restore
```bash
# Restore from a backup
make restore

# Or directly
bash scripts/restore.sh --file storage/exports/backup_20250101_120000.tar.gz
```

---

## CI/CD Pipeline

The GitHub Actions workflow in `infrastructure/github_actions/` handles:

1. **On Pull Request** — lint, type-check, unit tests
2. **On Merge to main** — integration tests, build Docker images, push to registry
3. **On Release Tag** — deploy to production via Terraform + kubectl

```
Push to PR → Lint + Test → Build Image → Push Registry
     ↓
Merge to main → Integration Tests → Tag & Deploy to Staging
     ↓
Manual approval → Deploy to Production
```

---

## Health Checks

All services expose `/health` endpoints. The API Gateway aggregates them:

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "healthy",
  "services": {
    "ingestion_service": "healthy",
    "embedding_service": "healthy",
    "vector_vault": "healthy",
    "telemetry_service": "healthy",
    "postgres": "healthy",
    "redis": "healthy"
  },
  "timestamp": "2025-07-11T12:00:00Z"
}
```

---

## Security Checklist (Production)

- [ ] All secrets in secrets manager — no plaintext in config files
- [ ] Row-level security enabled on all tenant tables
- [ ] JWT secret rotated from default
- [ ] API keys scoped per client
- [ ] TLS/SSL enabled on all public endpoints (Nginx handles termination)
- [ ] Rate limiting configured per tenant tier
- [ ] Database accessible only from Node 1 — no public access
- [ ] Grafana admin password changed
- [ ] All Docker images built from pinned base versions
- [ ] Firewall rules restrict inter-node traffic to known ports
