# DATA ENGINE — System Architecture

## Overview

The Data Engine is a distributed, AI-driven data processing platform built around four progressive phases. Each phase introduces a new layer of capability, from local data foundations through to a fully secured, cloud-deployed production system.

---

## Phase 1: Local Architecture

### Objective
Establish the foundational processing environment for local data ingestion, preprocessing, embedding generation, and vector persistence.

### Component Map

```
Raw Data Input
      │
      ▼
┌─────────────────────┐
│  Ingestion Service  │  FastAPI — handles uploads, request routing, payload preprocessing
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   Token Chunker     │  256–512 token windows with configurable overlap
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Embedding Pipeline │  Converts chunks → high-dimensional vectors via API providers
│  (OpenAI / Gemini / │
│   Voyage / Jina)    │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   Vector Vault      │  Local vector repository — stores embeddings + metadata
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ PostgreSQL+PGVector │  Persistent storage — similarity indexing, semantic search
└─────────────────────┘
```

### Key Services
| Service | Role |
|---------|------|
| `ingestion_service` | Request handling, data transformation, payload preprocessing |
| `chunking_service` | Text segmentation, context preservation, token windowing |
| `embedding_service` | API-based vector generation across multiple providers |
| `vector_vault` | Vector persistence, retrieval, and metadata management |

---

## Phase 2: Global Telemetry

### Objective
Introduce distributed intelligence for collecting external information and performing semantic analysis, gap detection, and adaptive schema generation.

### Component Map

```
External Sources (Search APIs, News, Social)
      │
      ▼
┌─────────────────────┐
│   Trend Scraper     │  Automated search API loops — Google, Bing, social, news
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Telemetry Service  │  FastAPI async workers — concurrent background collection
│  (Global Radar)     │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Semantic Engine    │  Cosine similarity, clustering, pattern analysis
│  (Vector Math)      │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   Gap Detection     │  Identifies missing information and optimisation opportunities
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Schema Factory     │  LLM-driven JSON-LD / metadata / ontology generation
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Synchronization    │  Merges all datasets into unified, consistent data pool
└─────────────────────┘
```

### Key Services
| Service | Role |
|---------|------|
| `telemetry_service` | Async workers, schedulers, distributed collection |
| `trend_scraper` | Search API consumption, trend monitoring |
| `semantic_engine` | Cosine similarity, clustering, ranking, reranking |
| `gap_detection` | Pattern recognition, gap identification, scoring |
| `schema_factory` | JSON-LD generation, metadata creation, LLM schema expansion |
| `synchronization` | Data sync, replication, cache management, event propagation |

---

## Phase 3: Central Command

### Objective
Provide centralised management, operational monitoring, and real-time system visibility.

### Component Map

```
┌─────────────────────────────────────────────┐
│              API Gateway                    │
│  Authentication · Rate Limiting · Routing   │
└──────────────────────┬──────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────────┐
│  Dashboard   │ │  Telemetry   │ │   Before/After   │
│  Backend     │ │  Stream View │ │   Gap Graph API  │
└──────────────┘ └──────────────┘ └──────────────────┘
        │
        ▼
  Admin Frontend (separate project)
```

### Key Services
| Service | Role |
|---------|------|
| `api_gateway` | Authentication, rate limiting, request routing, API key management |
| `dashboard_backend` | Analytics API, WebSocket streams, metrics aggregation |

---

## Phase 4: Production Deployment

### Objective
Transition to a secure, scalable cloud environment with SEO and GEO intelligence layers.

### Infrastructure Layout

```
                    ┌──────────────────┐
                    │   Load Balancer  │
                    │   (Nginx / ALB)  │
                    └────────┬─────────┘
                             │
               ┌─────────────┴─────────────┐
               ▼                           ▼
   ┌───────────────────────┐  ┌───────────────────────────┐
   │  Node 1               │  │  Node 2                   │
   │  Compute-Optimised    │  │  Memory-Optimised (DB)     │
   │  ─────────────────    │  │  ────────────────────────  │
   │  Python APIs          │  │  PostgreSQL + PGVector    │
   │  Admin Services       │  │  HNSW Indexing            │
   │  Telemetry Services   │  │  Row-Level Security       │
   │  Processing Workers   │  │  Production Vector Vault  │
   └───────────────────────┘  └───────────────────────────┘
```

### Intelligence Layers

```
Processed Vector Data
        │
        ├──────────────────────────────────────┐
        ▼                                      ▼
┌──────────────────┐                ┌─────────────────────┐
│   SEO Engine     │                │    GEO Engine       │
│ ──────────────── │                │ ─────────────────── │
│ Keyword Analysis │                │ Entity Mapping      │
│ Metadata Optim.  │                │ Semantic Optimiser  │
│ Schema Validation│                │ Citation Readiness  │
│ Search Indexing  │                │ LLM Visibility      │
└──────────────────┘                └─────────────────────┘
        │                                      │
        └──────────────────────────────────────┘
                            │
                            ▼
               ┌────────────────────────┐
               │  Testing & Validation  │
               │  SEO · GEO · Load ·    │
               │  Stress · Integration  │
               └────────────────────────┘
```

---

## Data Flow Summary

```
Ingest → Chunk → Embed → Store → Analyse → Detect Gaps
  → Generate Schemas → Sync → Serve via API Gateway
    → SEO/GEO Optimise → Test → Deploy
```

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| API Framework | FastAPI + Uvicorn |
| Database | PostgreSQL 16 + PGVector |
| Task Queue | Celery + Redis |
| Embeddings | OpenAI / Gemini / Voyage / Jina |
| Vector Math | NumPy, SciPy, scikit-learn, FAISS |
| Monitoring | Prometheus + Grafana |
| Container | Docker + Docker Compose |
| Orchestration | Kubernetes (production) |
| IaC | Terraform |
| CI/CD | GitHub Actions |
