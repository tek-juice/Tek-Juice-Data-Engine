-- DATA ENGINE — PGVector Setup & Optimisation
-- Run once after PostgreSQL installation to prepare vector capabilities.

-- ── Extension Installation 
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify installation
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';

-- ── HNSW Index Configuration 
-- HNSW provides approximate nearest-neighbour search with high recall.
-- Parameters:
--   m               = max connections per layer (default 16, higher = better recall, more memory)
--   ef_construction = build quality (default 64, higher = better index, slower build)
--   ef_search       = query quality (set at query time via GUC)

-- Embeddings HNSW index (cosine similarity)
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw ON embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Scraped trends HNSW index
CREATE INDEX IF NOT EXISTS idx_trends_hnsw ON scraped_trends
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- GEO entities HNSW index
CREATE INDEX IF NOT EXISTS idx_geo_entities_hnsw ON geo_entities
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── IVFFlat Alternative Index (for very large datasets) 
-- Use IVFFlat when dataset > 1M vectors and memory is constrained.
-- Must run ANALYZE after loading data before building IVFFlat index.
--
-- CREATE INDEX IF NOT EXISTS idx_embeddings_ivfflat ON embeddings
--     USING ivfflat (embedding vector_cosine_ops)
--     WITH (lists = 100);

-- ── Query Tuning 
-- Set ef_search at session/transaction level for query quality:
--   SET hnsw.ef_search = 100;  -- higher = better recall, slower query

-- ── PostgreSQL Memory Tuning for Vectors 
-- These settings belong in postgresql.conf or as ALTER SYSTEM commands.
-- Adjust based on available RAM on Node 2 (memory-optimised DB).

-- For 32GB RAM server (r6i.2xlarge):
-- ALTER SYSTEM SET shared_buffers = '8GB';
-- ALTER SYSTEM SET effective_cache_size = '24GB';
-- ALTER SYSTEM SET maintenance_work_mem = '2GB';
-- ALTER SYSTEM SET work_mem = '256MB';
-- ALTER SYSTEM SET max_parallel_workers_per_gather = 4;

-- ── Vacuum & Maintenance 
-- HNSW indexes require periodic vacuuming to stay performant.
-- Enable autovacuum with aggressive settings for vector tables:

ALTER TABLE embeddings SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);

ALTER TABLE scraped_trends SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);

-- ── Useful Diagnostic Queries 

-- Check vector index usage:
-- SELECT indexname, idx_scan, idx_tup_read, idx_tup_fetch
-- FROM pg_stat_user_indexes
-- WHERE indexname LIKE '%hnsw%';

-- Check embedding table size:
-- SELECT
--     pg_size_pretty(pg_total_relation_size('embeddings')) AS total_size,
--     COUNT(*) AS row_count
-- FROM embeddings;

-- Sample similarity search (cosine):
-- SELECT chunk_id, text, 1 - (embedding <=> '[0.1, 0.2, ...]'::vector) AS similarity
-- FROM embeddings
-- WHERE tenant_id = '<tenant_uuid>'
-- ORDER BY embedding <=> '[0.1, 0.2, ...]'::vector
-- LIMIT 10;
