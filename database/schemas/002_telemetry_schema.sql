-- DATA ENGINE — Telemetry & Trend Schema
-- Phase 2: Tables for telemetry events, scraped trends, gap analysis, schemas

-- ── Telemetry Events ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS telemetry_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,
    service         TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    duration_ms     INTEGER,
    status          TEXT DEFAULT 'success'
                        CHECK (status IN ('success', 'failure', 'warning')),
    request_id      UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_telemetry_tenant_id  ON telemetry_events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_event_type ON telemetry_events(event_type);
CREATE INDEX IF NOT EXISTS idx_telemetry_service    ON telemetry_events(service);
CREATE INDEX IF NOT EXISTS idx_telemetry_created_at ON telemetry_events(created_at DESC);

-- Partition by month for large-scale telemetry (PostgreSQL 16+)
-- (Partitioning DDL example — activate when data volume warrants)
-- CREATE TABLE telemetry_events_2025_07 PARTITION OF telemetry_events
--     FOR VALUES FROM ('2025-07-01') TO ('2025-08-01');

-- ── Scraped Trends ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scraped_trends (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT NOT NULL
                        CHECK (source IN ('google', 'bing', 'social_media', 'news', 'rss')),
    query           TEXT NOT NULL,
    title           TEXT,
    url             TEXT,
    snippet         TEXT,
    published_at    TIMESTAMPTZ,
    raw_content     TEXT,
    embedding       VECTOR(1536),
    relevance_score FLOAT,
    metadata        JSONB NOT NULL DEFAULT '{}',
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trends_source     ON scraped_trends(source);
CREATE INDEX IF NOT EXISTS idx_trends_scraped_at ON scraped_trends(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_trends_hnsw       ON scraped_trends
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── Gap Analysis Results ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gap_analysis_results (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    gap_score           FLOAT NOT NULL CHECK (gap_score BETWEEN 0.0 AND 1.0),
    severity            TEXT NOT NULL
                            CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    missing_topics      TEXT[] DEFAULT '{}',
    recommendations     TEXT[] DEFAULT '{}',
    reference_doc_ids   UUID[] DEFAULT '{}',
    before_coverage     FLOAT,
    after_coverage      FLOAT,
    metadata            JSONB NOT NULL DEFAULT '{}',
    analysed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gap_document_id ON gap_analysis_results(document_id);
CREATE INDEX IF NOT EXISTS idx_gap_tenant_id   ON gap_analysis_results(tenant_id);
CREATE INDEX IF NOT EXISTS idx_gap_severity    ON gap_analysis_results(severity);

-- ── Generated Schemas ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS generated_schemas (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    schema_type     TEXT NOT NULL,
    format          TEXT NOT NULL
                        CHECK (format IN ('jsonld', 'opengraph', 'twitter', 'schema_org')),
    content         JSONB NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    version         INTEGER NOT NULL DEFAULT 1,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_schemas_document_id ON generated_schemas(document_id);
CREATE INDEX IF NOT EXISTS idx_schemas_tenant_id   ON generated_schemas(tenant_id);
CREATE INDEX IF NOT EXISTS idx_schemas_type        ON generated_schemas(schema_type);

CREATE TRIGGER trg_schemas_updated_at
    BEFORE UPDATE ON generated_schemas
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── Sync Log ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sync_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sync_type       TEXT NOT NULL,
    status          TEXT NOT NULL
                        CHECK (status IN ('started', 'completed', 'failed')),
    records_synced  INTEGER DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sync_log_status     ON sync_log(status);
CREATE INDEX IF NOT EXISTS idx_sync_log_started_at ON sync_log(started_at DESC);
