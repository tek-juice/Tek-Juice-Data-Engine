-- DATA ENGINE — Initial Database Schema
-- Phase 1: Core tables for documents, chunks, embeddings, and tenants
-- Run after extensions are enabled (pgvector, pg_trgm, pgcrypto)

-- ── Extensions 
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Tenants 
CREATE TABLE IF NOT EXISTS tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    tier            TEXT NOT NULL DEFAULT 'standard'
                        CHECK (tier IN ('standard', 'premium', 'enterprise')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Users 
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email           TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    full_name       TEXT,
    role            TEXT NOT NULL DEFAULT 'viewer'
                        CHECK (role IN ('admin', 'editor', 'viewer')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ── API Keys 
CREATE TABLE IF NOT EXISTS api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    key_hash        TEXT NOT NULL UNIQUE,        -- store hash, never plaintext
    key_prefix      TEXT NOT NULL,               -- e.g. de_abc123 (first 12 chars)
    name            TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at      TIMESTAMPTZ,
    last_used_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_tenant_id ON api_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);

-- ── Documents 
CREATE TABLE IF NOT EXISTS documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    source_type     TEXT NOT NULL
                        CHECK (source_type IN ('pdf','docx','txt','markdown','json','csv','html')),
    status          TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN (
                            'queued','preprocessing','chunking',
                            'embedding','storing','completed','failed','deleted'
                        )),
    file_size_bytes BIGINT,
    mime_type       TEXT,
    raw_text        TEXT,
    language        TEXT,
    chunk_count     INTEGER DEFAULT 0,
    error_message   TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}',
    storage_path    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant_id  ON documents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_documents_status     ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);

-- ── Chunks 
CREATE TABLE IF NOT EXISTS chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    token_count     INTEGER NOT NULL,
    char_start      INTEGER,
    char_end        INTEGER,
    strategy        TEXT NOT NULL DEFAULT 'token'
                        CHECK (strategy IN ('token', 'semantic')),
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tenant_id   ON chunks(tenant_id);
CREATE INDEX IF NOT EXISTS idx_chunks_text_trgm   ON chunks USING GIN (text gin_trgm_ops);

-- ── Embeddings (Vector Vault)
-- NOTE: dimensions column drives which vector column is populated.
-- Each provider/model combination uses its own namespace column to prevent
-- cross-model cosine distance calculations (<=>) which produce meaningless scores.
-- Supported namespaces:
--   embedding_768  → Gemini text-embedding-004 (768d), Jina v2 base (768d)
--   embedding_1536 → OpenAI text-embedding-3-small / ada-002 (1536d), Voyage large-2 (1536d)
--   embedding_1024 → Voyage-2 (1024d)
--   embedding_3072 → OpenAI text-embedding-3-large (3072d)
CREATE TABLE IF NOT EXISTS embeddings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id        UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- Namespace columns — only one is populated per row based on provider/model
    embedding_768   VECTOR(768),    -- Gemini, Jina
    embedding_1536  VECTOR(1536),   -- OpenAI small/ada, Voyage large-2
    embedding_1024  VECTOR(1024),   -- Voyage-2
    embedding_3072  VECTOR(3072),   -- OpenAI large
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    model_version   TEXT NOT NULL DEFAULT '1',
    dimensions      INTEGER NOT NULL,
    -- Prevent cross-model distance calculations at DB level
    CONSTRAINT chk_dimension_namespace CHECK (
        (dimensions = 768  AND embedding_768  IS NOT NULL AND embedding_1536 IS NULL AND embedding_1024 IS NULL AND embedding_3072 IS NULL) OR
        (dimensions = 1536 AND embedding_1536 IS NOT NULL AND embedding_768  IS NULL AND embedding_1024 IS NULL AND embedding_3072 IS NULL) OR
        (dimensions = 1024 AND embedding_1024 IS NOT NULL AND embedding_768  IS NULL AND embedding_1536 IS NULL AND embedding_3072 IS NULL) OR
        (dimensions = 3072 AND embedding_3072 IS NOT NULL AND embedding_768  IS NULL AND embedding_1536 IS NULL AND embedding_1024 IS NULL)
    ),
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HNSW indexes per namespace — never mix dimensions in one index
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_768  ON embeddings USING hnsw (embedding_768  vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_768  IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_1536 ON embeddings USING hnsw (embedding_1536 vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_1536 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_1024 ON embeddings USING hnsw (embedding_1024 vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_1024 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw_3072 ON embeddings USING hnsw (embedding_3072 vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE embedding_3072 IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_embeddings_tenant_id   ON embeddings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_document_id ON embeddings(document_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_model       ON embeddings(model);
CREATE INDEX IF NOT EXISTS idx_embeddings_provider    ON embeddings(provider);

-- ── Timestamps auto-update trigger 
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_tenants_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
