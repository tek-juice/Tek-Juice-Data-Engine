# DATA ENGINE — API Reference

All services expose REST APIs via FastAPI. The API Gateway (`port 8000`) is the single entry point for external clients. Internal services communicate directly over the Docker network.

---

## Authentication

All requests to the API Gateway require either:
- **Bearer Token** — `Authorization: Bearer <jwt_token>`
- **API Key** — `X-API-Key: <api_key>`

Obtain a token via the auth endpoint:

```http
POST /auth/token
Content-Type: application/json

{
  "username": "user@example.com",
  "password": "your-password"
}
```

Response:
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

---

## API Gateway — Port 8000

### Health
```http
GET /health
```
Returns system health status across all registered services.

### Auth
```http
POST /auth/token          # Obtain JWT
POST /auth/refresh         # Refresh JWT
POST /auth/revoke          # Revoke token
POST /auth/api-keys        # Generate API key
DELETE /auth/api-keys/{id} # Revoke API key
```

---

## Ingestion Service — Port 8001

### Upload Document
```http
POST /ingest/upload
Content-Type: multipart/form-data
Authorization: Bearer <token>

file: <binary>
source_type: pdf | docx | txt | json | csv
tenant_id: <uuid>
```

Response:
```json
{
  "document_id": "uuid",
  "status": "queued",
  "chunks_estimated": 42
}
```

### Get Ingestion Status
```http
GET /ingest/status/{document_id}
```

### List Documents
```http
GET /ingest/documents?tenant_id=<uuid>&page=1&page_size=20
```

### Delete Document
```http
DELETE /ingest/documents/{document_id}
```

---

## Chunking Service — Port 8002

### Chunk Text
```http
POST /chunk/token
Content-Type: application/json

{
  "text": "...",
  "min_tokens": 256,
  "max_tokens": 512,
  "overlap_tokens": 64
}
```

### Semantic Chunk
```http
POST /chunk/semantic
Content-Type: application/json

{
  "text": "...",
  "similarity_threshold": 0.85
}
```

---

## Embedding Service — Port 8003

### Generate Embeddings
```http
POST /embed
Content-Type: application/json

{
  "texts": ["text1", "text2"],
  "provider": "openai",
  "model": "text-embedding-3-small"
}
```

Response:
```json
{
  "embeddings": [[0.123, ...], [0.456, ...]],
  "model": "text-embedding-3-small",
  "dimensions": 1536,
  "usage": { "total_tokens": 42 }
}
```

### List Providers
```http
GET /embed/providers
```

---

## Vector Vault — Port 8004

### Store Vectors
```http
POST /vectors/store
Content-Type: application/json

{
  "document_id": "uuid",
  "chunks": [
    {
      "chunk_id": "uuid",
      "text": "...",
      "embedding": [0.123, ...],
      "metadata": {}
    }
  ]
}
```

### Similarity Search
```http
POST /vectors/search
Content-Type: application/json

{
  "query_embedding": [0.123, ...],
  "top_k": 10,
  "similarity_threshold": 0.75,
  "tenant_id": "uuid",
  "filters": {}
}
```

Response:
```json
{
  "results": [
    {
      "chunk_id": "uuid",
      "text": "...",
      "similarity": 0.94,
      "metadata": {}
    }
  ]
}
```

### Delete Vectors
```http
DELETE /vectors/{document_id}
```

---

## Telemetry Service — Port 8005

### Get Telemetry Stream
```http
GET /telemetry/stream
Accept: text/event-stream
```

### Get Metrics Summary
```http
GET /telemetry/metrics?from=2025-01-01&to=2025-12-31
```

### List Active Workers
```http
GET /telemetry/workers
```

---

## Trend Scraper — Port 8006

### Trigger Manual Scrape
```http
POST /scrape/run
Content-Type: application/json

{
  "query": "AI data processing trends",
  "sources": ["google", "bing", "news"],
  "limit": 50
}
```

### Get Latest Trends
```http
GET /scrape/trends?topic=<string>&limit=20
```

---

## Semantic Engine — Port 8007

### Compare Vectors
```http
POST /semantic/compare
Content-Type: application/json

{
  "vector_a": [0.123, ...],
  "vector_b": [0.456, ...]
}
```

### Cluster Vectors
```http
POST /semantic/cluster
Content-Type: application/json

{
  "vectors": [[...], [...]],
  "n_clusters": 5
}
```

### Rank Results
```http
POST /semantic/rank
Content-Type: application/json

{
  "query_vector": [...],
  "candidates": [{"id": "uuid", "vector": [...]}]
}
```

---

## Gap Detection — Port 8008

### Analyse Gaps
```http
POST /gaps/analyse
Content-Type: application/json

{
  "document_id": "uuid",
  "reference_corpus_ids": ["uuid1", "uuid2"]
}
```

Response:
```json
{
  "gap_score": 0.42,
  "missing_topics": ["topic_a", "topic_b"],
  "recommendations": ["Add content about X", "Expand section Y"],
  "optimisation_priority": "high"
}
```

### Get Gap Report
```http
GET /gaps/report/{document_id}
```

---

## Schema Factory — Port 8009

### Generate JSON-LD Schema
```http
POST /schema/jsonld
Content-Type: application/json

{
  "entity_type": "Article",
  "content": "...",
  "metadata": {}
}
```

### Generate Metadata
```http
POST /schema/metadata
Content-Type: application/json

{
  "content": "...",
  "target_format": "opengraph | twitter | schema_org"
}
```

---

## Dashboard Backend — Port 8011

### Get System Overview
```http
GET /dashboard/overview
```

### Get Before/After Gap Comparison
```http
GET /dashboard/gap-comparison/{document_id}
```

### WebSocket — Live Activity Feed
```
WS /dashboard/ws/activity
```

---

## SEO Engine — Port 8012

### Analyse Keywords
```http
POST /seo/keywords
Content-Type: application/json

{
  "content": "...",
  "target_keywords": ["keyword1", "keyword2"]
}
```

### Validate Schema
```http
POST /seo/validate
Content-Type: application/json

{
  "schema": { "@context": "...", "@type": "..." }
}
```

### Generate Sitemap
```http
POST /seo/sitemap
Content-Type: application/json

{
  "urls": ["https://example.com/page1"]
}
```

---

## GEO Engine — Port 8013

### Map Entities
```http
POST /geo/entities
Content-Type: application/json

{
  "content": "...",
  "entity_types": ["Person", "Organisation", "Place"]
}
```

### Optimise for LLM Visibility
```http
POST /geo/optimise
Content-Type: application/json

{
  "content": "...",
  "target_models": ["gpt-4", "gemini", "claude"]
}
```

### Build Knowledge Graph
```http
POST /geo/knowledge-graph
Content-Type: application/json

{
  "entities": [...],
  "relationships": [...]
}
```

---

## Error Responses

All endpoints return standard error shapes:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Field 'text' is required.",
    "details": {},
    "request_id": "uuid"
  }
}
```

### HTTP Status Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request / Validation Error |
| 401 | Unauthorised |
| 403 | Forbidden |
| 404 | Not Found |
| 422 | Unprocessable Entity |
| 429 | Rate Limit Exceeded |
| 500 | Internal Server Error |
| 503 | Service Unavailable |

---

## Rate Limiting

Default limits (configurable per tenant):
- **Standard**: 100 requests/minute
- **Premium**: 1000 requests/minute
- **Internal**: Unlimited

Rate limit headers are returned on every response:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1720000000
```
