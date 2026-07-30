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

## Gap Detection + Quality Score — Port 8008

### Analyse Content Gaps
```http
POST /api/v1/gaps/analyze
Content-Type: application/json

{
  "document_id": "uuid",
  "tenant_id": "uuid",
  "gap_threshold": 0.40,
  "max_gaps": 20
}
```

Response:
```json
{
  "gap_score": 0.42,
  "severity": "high",
  "missing_topics": ["AI inventory management", "multi-channel sync"],
  "recommendations": ["Add content about X", "Expand section Y"],
  "before_coverage": 0.58,
  "after_coverage": null
}
```

### Build Intent-Based Content Clusters
```http
POST /api/v1/gaps/clusters
Content-Type: application/json

{
  "document_id": "uuid",
  "tenant_id": "uuid",
  "missing_topics": ["AI inventory management"],
  "document_content": "...",
  "max_clusters_per_topic": 4
}
```

### Trigger Auto Gap-Closure Plan
```http
POST /api/v1/gaps/close/{document_id}?tenant_id=uuid
```

### Get Current Closure Plan
```http
GET /api/v1/gaps/close-actions/{document_id}?tenant_id=uuid
```

### Trigger LLM Writing Agent
```http
POST /api/v1/gaps/write/{document_id}?tenant_id=uuid
```
Dispatches the Gemini writing agent to generate content drafts for all open intent clusters. Each draft is scored by the GEO, AEO, and Quality Score engines. Drafts below QS 8 are automatically rewritten before saving.

### Get Generated Content Drafts
```http
GET /api/v1/gaps/drafts/{document_id}?tenant_id=uuid&status=draft
```

Response includes per-draft scores:
```json
{
  "drafts": [{
    "topic": "AI inventory management",
    "intent": "definitional",
    "draft_text": "...",
    "geo_score": 78.5,
    "aeo_score": 71.2,
    "composite_score": 74.8,
    "quality_score": 8.3,
    "beats_paid_ads": true,
    "projected_position": "#1–#2 — above most paid ads"
  }]
}
```

### Gap Analysis History
```http
GET /api/v1/gaps/history/{document_id}?tenant_id=uuid
```

---

### Google Ads Quality Score Engine
```http
POST /api/v1/gaps/quality-score
Content-Type: application/json

{
  "content": "Inventory management software reduces stockouts by 40%...",
  "title": "Best Inventory Management Software 2024",
  "meta_description": "Discover how inventory software cuts costs by 40%...",
  "query": "inventory management software",
  "target_keywords": ["inventory management", "stock control"],
  "monthly_search_volume": 22000
}
```

Maps Google's Ad Rank formula to organic content — computes a Quality Score (1–10) and simulates position vs every paid ad.

Response:
```json
{
  "quality_score": 8.3,
  "label": "Strong",
  "beats_paid_ads": true,
  "projected_position": "#1–#2 — above most paid ads",
  "score_to_next_band": 0.7,
  "dimensions": {
    "snippet_attractiveness": { "score": 8.5, "status": "Above Average", "fixes": [] },
    "keyword_alignment":      { "score": 8.0, "status": "Above Average", "fixes": [] },
    "content_experience":     { "score": 8.3, "status": "Above Average", "fixes": [] }
  },
  "rank_simulation": {
    "estimated_position": 2,
    "paid_ads_beaten": 3,
    "beats_all_paid_ads": false,
    "ctr_multiplier": 2.0,
    "traffic_multiplier": "2.0× more traffic at position #1 vs current position 2",
    "ad_benchmarks": [
      { "ad_position": "Ad #1 (top)", "ad_typical_qs": 8.5, "beats_this_ad": false, "qs_gap": 0.2 }
    ],
    "uplift_steps": [
      { "to_qs": 9.0, "milestone": "Featured Snippet + Position 1", "ctr_gain": "+15.6% CTR" }
    ]
  },
  "action_plan": [
    "Increase QS 8.3 → 8.5 (+0.2 pts) to beat Ad #1 (top).",
    "Reach QS 9.5 to enter Google AI Overviews — displays ABOVE all paid ads at zero cost."
  ]
}
```

| QS | Position | vs Paid Ads |
|---|---|---|
| 9.5–10 | AI Overview (#0) | Above ALL ads, zero cost |
| 8.5–9.5 | #1 + Featured Snippet | Above all paid ads |
| 8.0–8.5 | #1–#2 | Above most paid ads |
| 7.0–8.0 | #2–#3 | Competitive with top ads |
| 5.0–7.0 | #4–#6 | Level with or below ads |
| < 5.0 | #7–Page 2 | Below all paid ads |

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
  "target_models": ["gemini", "google_ai_overviews", "perplexity", "chatgpt", "bing_copilot", "claude"]
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
