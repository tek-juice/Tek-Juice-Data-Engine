# DATA ENGINE

A standalone, intelligent, scalable, and production-ready data processing ecosystem designed to collect, transform, analyse, compare, and optimise information using modern vector technologies and AI-driven workflows.

---

## Overview

The Data Engine combines:
- **Data Engineering** — ingestion, preprocessing, chunking, and embedding
- **Telemetry Intelligence** — global radar, trend scraping, async workers
- **Vector Processing** — PGVector-backed semantic storage, HNSW indexing
- **Semantic Analysis** — cosine similarity, clustering, gap detection
- **Centralised Administration** — API gateway, dashboard backend, real-time metrics
- **SEO Intelligence** — keyword analysis, metadata optimisation, schema validation
- **GEO Intelligence** — entity mapping, semantic optimisation, LLM visibility

---

## Architecture Phases

| Phase | Name | Focus |
|-------|------|-------|
| 1 | Local Architecture | Core ingestion, chunking, embedding, vector storage |
| 2 | Global Telemetry | Trend scraping, semantic comparison, gap detection, LLM schema generation |
| 3 | Central Command | Admin dashboard, API manager, telemetry viewer, gap graphs |
| 4 | Production Deployment | Cloud provisioning, row-level security, SEO/GEO layers, live testing |

---

## Project Structure

```
DATA_ENGINE/
├── configs/          # Central configuration (settings, DB, security, logging)
├── database/         # Migrations, schemas, seeds, pgvector setup
├── docs/             # Architecture, API reference, deployment, pipeline docs
├── infrastructure/   # Nginx, Docker, Kubernetes, Terraform, CI/CD
├── monitoring/       # Prometheus, Grafana, Alertmanager, healthchecks
├── scripts/          # Install, migrate, backup, restore, deploy scripts
├── services/         # All microservices (ingestion, embedding, telemetry, SEO, GEO, ...)
├── shared/           # Shared models, utils, middleware, auth, validators
├── storage/          # Uploads, processed data, vectors, exports, logs
├── tests/            # Unit, integration, performance, end-to-end tests
└── workers/          # Celery workers, cron jobs, background tasks, queues
```

---

## Quick Start

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd DATA_ENGINE

# 2. Copy environment config
cp .env.example .env

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start all services
docker-compose up -d

# 5. Run database migrations
make migrate

# 6. Verify health
make health
```

---

## Requirements

- Python 3.11+
- PostgreSQL 15+ with PGVector extension
- Docker & Docker Compose
- Redis (for Celery task queue)

---

## Development Commands

```bash
make install      # Install all dependencies
make migrate      # Run database migrations
make seed         # Seed initial data
make test         # Run full test suite
make lint         # Run linting
make format       # Auto-format code
make health       # Check all service health endpoints
make logs         # Tail all service logs
make backup       # Backup database and vectors
make deploy       # Deploy to production
```

---

## Services

| Service | Port | Description |
|---------|------|-------------|
| ingestion_service | 8001 | Data ingestion and preprocessing |
| chunking_service | 8002 | Token and semantic chunking |
| embedding_service | 8003 | Vector embedding generation |
| vector_vault | 8004 | Vector storage and retrieval |
| telemetry_service | 8005 | Async telemetry collection |
| trend_scraper | 8006 | External trend monitoring |
| semantic_engine | 8007 | Cosine similarity and ranking |
| gap_detection | 8008 | Gap analysis and recommendations |
| schema_factory | 8009 | JSON-LD and metadata generation |
| synchronization | 8010 | Data sync and replication |
| api_gateway | 8000 | Central API gateway |
| dashboard_backend | 8011 | Admin dashboard API |
| seo_engine | 8012 | SEO intelligence layer |
| geo_engine | 8013 | GEO intelligence layer |

---

## License

See [LICENSE](./LICENSE) for details.
