# DATA ENGINE — Vector Pipeline

The vector pipeline is the core processing backbone of the Data Engine. It transforms raw text into semantically searchable vector representations and persists them for downstream analysis.

---

## Pipeline Overview

```
Raw Document
     │
     ▼
┌─────────────────────┐
│  1. Ingestion        │  Validate, normalise, extract text from PDF/DOCX/TXT/JSON
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  2. Preprocessing    │  Clean text, remove noise, normalise whitespace
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  3. Token Chunking   │  Split into 256–512 token windows with 64-token overlap
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  4. Embedding        │  Send chunks to embedding provider → receive float vectors
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  5. Vector Storage   │  Persist vectors + metadata to PGVector (HNSW indexed)
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  6. Semantic Search  │  ANN search — cosine similarity against query vectors
└─────────────────────┘
```

---

## Stage 1: Ingestion

The `ingestion_service` accepts documents via multipart upload or JSON payloads. Supported formats:
- PDF
- DOCX
- TXT / Markdown
- JSON
- CSV

Text is extracted and normalised before entering the chunking stage. Large files are processed asynchronously via Celery workers.

---

## Stage 2: Preprocessing

Before chunking, text undergoes:
- HTML/markup stripping
- Unicode normalisation (NFC)
- Whitespace normalisation
- Removal of null bytes and control characters
- Language detection (optional metadata tag)

---

## Stage 3: Token Chunking

### Token Chunker
Splits text into fixed token windows using `tiktoken` (cl100k_base encoding by default).

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_tokens` | 256 | Minimum tokens per chunk |
| `max_tokens` | 512 | Maximum tokens per chunk |
| `overlap_tokens` | 64 | Overlap between adjacent chunks |

The overlap preserves context at chunk boundaries, reducing information loss during embedding.

### Semantic Chunker
An alternative chunker that uses embedding similarity to find natural break points rather than fixed token counts. Chunks are split when the cosine similarity between adjacent sentences drops below a configured threshold.

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `similarity_threshold` | 0.85 | Split threshold |
| `min_chunk_size` | 100 | Minimum characters per chunk |

---

## Stage 4: Embedding Generation

The embedding pipeline sends chunk batches to the configured provider.

### Supported Providers

| Provider | Model | Dimensions | Notes |
|----------|-------|-----------|-------|
| OpenAI | text-embedding-3-small | 1536 | Default |
| OpenAI | text-embedding-3-large | 3072 | Higher quality |
| Gemini | embedding-001 | 768 | Google |
| Voyage | voyage-large-2 | 1536 | Voyage AI |
| Jina | jina-embeddings-v2-base-en | 768 | Open-weights option |

### Batching
Chunks are batched before sending to the embedding API:
- Default batch size: 100 chunks per request
- Retry on rate limit with exponential backoff (max 5 retries)
- Failed chunks are re-queued via Celery

### Provider Fallback
If the primary provider fails, the pipeline falls back to the next configured provider automatically.

---

## Stage 5: Vector Storage

### PGVector Schema
Each embedding is stored as a row in the `embeddings` table:

```sql
CREATE TABLE embeddings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id    UUID NOT NULL,
    tenant_id   UUID NOT NULL,
    text        TEXT NOT NULL,
    embedding   VECTOR(1536),
    model       TEXT NOT NULL,
    provider    TEXT NOT NULL,
    token_count INTEGER,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

### HNSW Index
```sql
CREATE INDEX ON embeddings USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

HNSW (Hierarchical Navigable Small World) provides approximate nearest-neighbour search with:
- Sub-linear query time
- High recall at k=10 (>95%)
- Memory-efficient indexing

---

## Stage 6: Semantic Search

### Query Flow
```
User Query Text
      │
      ▼  (embed query using same provider/model)
Query Vector [float[1536]]
      │
      ▼  (ANN search via HNSW index)
Top-K Similar Chunks
      │
      ▼  (optional reranking via cross-encoder)
Ranked Results
```

### SQL Search Query
```sql
SELECT
    chunk_id,
    text,
    metadata,
    1 - (embedding <=> $1::vector) AS similarity
FROM embeddings
WHERE
    tenant_id = $2
    AND 1 - (embedding <=> $1::vector) >= $3
ORDER BY embedding <=> $1::vector
LIMIT $4;
```

---

## Pipeline Telemetry

Every stage emits metrics to Prometheus:
- `pipeline_documents_ingested_total`
- `pipeline_chunks_created_total`
- `pipeline_embeddings_generated_total`
- `pipeline_embedding_latency_seconds`
- `pipeline_search_latency_seconds`
- `pipeline_errors_total` (labelled by stage)

---

## Configuration Reference

All pipeline settings are in `configs/settings.py` and can be overridden via environment variables:

```python
CHUNK_MIN_TOKENS = 256
CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 64
DEFAULT_EMBEDDING_PROVIDER = "openai"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536
VECTOR_SIMILARITY_THRESHOLD = 0.75
VECTOR_TOP_K = 10
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64
```
