# DATA ENGINE — Telemetry & Monitoring

---

## Overview

The telemetry layer provides full observability across the Data Engine platform. It covers:
- **Global Radar** — async workers continuously collecting external trend data
- **System Metrics** — service-level performance via Prometheus
- **Real-time Streaming** — live activity feeds via WebSocket and SSE
- **Dashboards** — Grafana visualisations for all key metrics
- **Alerting** — Alertmanager rules for critical system events

---

## Telemetry Service (Global Radar)

The `telemetry_service` runs FastAPI async workers that collect data continuously in the background.

### Architecture

```
Scheduler (APScheduler)
        │
        ├── Trend Scrape Job    (every 1 hour)
        ├── Gap Analysis Job    (every 6 hours)
        ├── Sync Job            (every 15 minutes)
        └── Health Check Job   (every 1 minute)
                │
                ▼
        Async Worker Pool (configurable, default: 4 workers)
                │
                ▼
        Redis Queue (task buffering)
                │
                ▼
        Collectors (Google, Bing, News, Social)
                │
                ▼
        Vector Vault (processed and stored)
```

### Worker Configuration
| Setting | Default | Description |
|---------|---------|-------------|
| `TELEMETRY_WORKER_COUNT` | 4 | Concurrent async workers |
| `TELEMETRY_QUEUE_MAX_SIZE` | 10000 | Max items in queue before backpressure |
| `TELEMETRY_FLUSH_INTERVAL_SECONDS` | 30 | How often to flush buffered metrics |

---

## Prometheus Metrics

All services expose a `/metrics` endpoint scraped by Prometheus every 15 seconds.

### Application Metrics

**Ingestion**
```
data_engine_documents_ingested_total{tenant, source_type}
data_engine_ingestion_duration_seconds{percentile}
data_engine_ingestion_errors_total{error_type}
```

**Chunking**
```
data_engine_chunks_created_total{chunker_type}
data_engine_chunk_token_size_histogram{bucket}
```

**Embedding**
```
data_engine_embeddings_generated_total{provider, model}
data_engine_embedding_latency_seconds{provider, percentile}
data_engine_embedding_token_usage_total{provider}
data_engine_embedding_errors_total{provider, error_type}
```

**Vector Vault**
```
data_engine_vectors_stored_total{tenant}
data_engine_search_requests_total{tenant}
data_engine_search_latency_seconds{percentile}
data_engine_similarity_score_histogram{bucket}
```

**Gap Detection**
```
data_engine_gaps_detected_total{severity}
data_engine_gap_analysis_duration_seconds
data_engine_schemas_generated_total
```

**API Gateway**
```
data_engine_http_requests_total{method, path, status}
data_engine_http_request_duration_seconds{percentile}
data_engine_rate_limit_exceeded_total{tenant}
data_engine_auth_failures_total{reason}
```

### Infrastructure Metrics
Standard node_exporter metrics cover CPU, memory, disk, and network at the OS level.

---

## Grafana Dashboards

Pre-built dashboards are provisioned automatically from `monitoring/grafana/provisioning/`.

| Dashboard | Description |
|-----------|-------------|
| **System Overview** | High-level platform health, request rates, error rates |
| **Pipeline Throughput** | Documents ingested, chunks created, embeddings generated per hour |
| **Vector Vault** | Storage growth, search latency, similarity distributions |
| **Telemetry Radar** | Trend collection rates, scraper status, queue depth |
| **Gap Analysis** | Gap detection rates, schema generation, optimisation scores |
| **SEO/GEO Performance** | Keyword coverage, entity mapping scores, LLM visibility scores |
| **Infrastructure** | CPU, memory, disk, network per node |

Access Grafana at `http://localhost:3000` (default credentials: admin / see `.env`).

---

## Alertmanager Rules

Critical alerts defined in `monitoring/alertmanager/`:

| Alert | Condition | Severity |
|-------|-----------|----------|
| `ServiceDown` | Service `/health` returns non-200 for >1 min | Critical |
| `HighErrorRate` | Error rate >5% over 5 min | Warning |
| `EmbeddingProviderFailure` | All embedding providers failing | Critical |
| `DatabaseConnectionPool` | Pool utilisation >90% | Warning |
| `RedisQueueDepth` | Queue depth >8000 items | Warning |
| `DiskSpaceVectors` | Vector storage >80% full | Warning |
| `SearchLatencyHigh` | p99 search latency >2s | Warning |
| `TelemetryWorkerDown` | No telemetry events for >10 min | Warning |

Alerts are routed to configured notification channels (email, Slack, PagerDuty).

---

## Real-time Telemetry Stream

The dashboard backend streams live system events via WebSocket and Server-Sent Events.

### WebSocket Connection
```javascript
const ws = new WebSocket('ws://localhost:8011/dashboard/ws/activity');
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // { type: "ingestion", document_id: "uuid", status: "completed", timestamp: "..." }
};
```

### SSE Stream
```http
GET /telemetry/stream
Accept: text/event-stream
```

Event types:
- `document.ingested`
- `chunk.created`
- `embedding.generated`
- `vector.stored`
- `search.performed`
- `gap.detected`
- `schema.generated`
- `scraper.completed`
- `error.occurred`

---

## Healthcheck Endpoints

Every service exposes:
```http
GET /health
```

Response format:
```json
{
  "status": "healthy",
  "service": "embedding_service",
  "version": "1.0.0",
  "uptime_seconds": 3600,
  "checks": {
    "database": "ok",
    "redis": "ok",
    "openai_api": "ok"
  },
  "timestamp": "2025-07-11T12:00:00Z"
}
```

The API Gateway aggregates all service health checks at `GET /health`.

---

## Logging

All services use structured JSON logging via `structlog`.

### Log Format
```json
{
  "timestamp": "2025-07-11T12:00:00.000Z",
  "level": "info",
  "service": "ingestion_service",
  "event": "document_ingested",
  "document_id": "uuid",
  "tenant_id": "uuid",
  "duration_ms": 142,
  "request_id": "uuid"
}
```

### Log Levels
| Level | When Used |
|-------|-----------|
| DEBUG | Detailed internal state (dev only) |
| INFO | Normal operations, key business events |
| WARNING | Non-fatal issues, degraded performance |
| ERROR | Failures that affect a request |
| CRITICAL | System-level failures requiring immediate attention |

Logs are written to `storage/logs/` and can be forwarded to any log aggregator (ELK, Loki, CloudWatch).

---

## Telemetry Before/After Gap Visualisation

The dashboard backend provides a gap comparison view showing:
- **Before**: Content coverage before gap detection + schema generation
- **After**: Content coverage after optimisation applied

This is exposed at `GET /dashboard/gap-comparison/{document_id}` and renders as a timeline graph in the admin dashboard.
