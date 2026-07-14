-- DATA ENGINE — Migration 001: Fix Vector Namespace Columns
-- Replaces single VECTOR(1536) columns with per-dimension namespace columns.
-- Run this if the initial schema was already applied before this fix.

BEGIN;

-- ── embeddings table ──────────────────────────────────────────────────────
ALTER TABLE embeddings
    ADD COLUMN IF NOT EXISTS embedding_768   VECTOR(768),
    ADD COLUMN IF NOT EXISTS embedding_1536  VECTOR(1536),
    ADD COLUMN IF NOT EXISTS embedding_1024  VECTOR(1024),
    ADD COLUMN IF NOT EXISTS embedding_3072  VECTOR(3072),
    ADD COLUMN IF NOT EXISTS model_version   TEXT NOT NULL DEFAULT '1';

-- Migrate existing 1536-dim data into the correct namespace column
UPDATE embeddings
SET embedding_1536 = embedding
WHERE dimensions = 1536 AND embedding IS NOT NULL;

-- Drop the old monolithic column
ALTER TABLE embeddings DROP COLUMN IF EXISTS embedding;

-- Alter dimensions default (no longer 1536)
ALTER TABLE embeddings ALTER COLUMN dimensions DROP DEFAULT;

-- New HNSW indexes per namespace
DROP INDEX IF EXISTS idx_embeddings_hnsw;
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_768  ON embeddings USING hnsw (embedding_768  vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_768  IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_1536 ON embeddings USING hnsw (embedding_1536 vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_1536 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_1024 ON embeddings USING hnsw (embedding_1024 vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_1024 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_3072 ON embeddings USING hnsw (embedding_3072 vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_3072 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_embeddings_model     ON embeddings(model);
CREATE INDEX IF NOT EXISTS idx_embeddings_provider  ON embeddings(provider);

-- ── scraped_trends table ─────────────────────────────────────────────────
ALTER TABLE scraped_trends
    ADD COLUMN IF NOT EXISTS embedding_768   VECTOR(768),
    ADD COLUMN IF NOT EXISTS embedding_1536  VECTOR(1536),
    ADD COLUMN IF NOT EXISTS embedding_model TEXT,
    ADD COLUMN IF NOT EXISTS embedding_dims  INTEGER;

UPDATE scraped_trends
SET embedding_1536 = embedding, embedding_dims = 1536
WHERE embedding IS NOT NULL;

ALTER TABLE scraped_trends DROP COLUMN IF EXISTS embedding;

DROP INDEX IF EXISTS idx_trends_hnsw;
CREATE INDEX IF NOT EXISTS idx_trends_hnsw_768  ON scraped_trends USING hnsw (embedding_768  vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_768  IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trends_hnsw_1536 ON scraped_trends USING hnsw (embedding_1536 vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_1536 IS NOT NULL;

-- ── geo_entities table ───────────────────────────────────────────────────
ALTER TABLE geo_entities
    ADD COLUMN IF NOT EXISTS embedding_768   VECTOR(768),
    ADD COLUMN IF NOT EXISTS embedding_1536  VECTOR(1536),
    ADD COLUMN IF NOT EXISTS embedding_model TEXT,
    ADD COLUMN IF NOT EXISTS embedding_dims  INTEGER,
    ADD COLUMN IF NOT EXISTS same_as_urls    TEXT[] DEFAULT '{}';

UPDATE geo_entities
SET embedding_1536 = embedding, embedding_dims = 1536
WHERE embedding IS NOT NULL;

ALTER TABLE geo_entities DROP COLUMN IF EXISTS embedding;

DROP INDEX IF EXISTS idx_geo_entities_hnsw;
CREATE INDEX IF NOT EXISTS idx_geo_entities_hnsw_768  ON geo_entities USING hnsw (embedding_768  vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_768  IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_geo_entities_hnsw_1536 ON geo_entities USING hnsw (embedding_1536 vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_1536 IS NOT NULL;

COMMIT;
