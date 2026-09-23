-- ─────────────────────────────────────────────────────────────────────────────
-- 008_gap_content_drafts.sql
-- LLM-generated content sections produced by the writing agent.
-- Each row is one content section (topic × intent) for a document.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS gap_content_drafts (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID        NOT NULL REFERENCES documents(id)  ON DELETE CASCADE,
    tenant_id       UUID        NOT NULL REFERENCES tenants(id)    ON DELETE CASCADE,

    -- What the draft covers
    topic           TEXT        NOT NULL,
    intent          TEXT        NOT NULL,   -- definitional|procedural|causal|comparative|quantitative|commercial|troubleshooting|authority
    priority        INTEGER     NOT NULL DEFAULT 99,

    -- Generated content
    draft_text      TEXT        NOT NULL,
    word_count      INTEGER     NOT NULL DEFAULT 0,

    -- Metadata from the closure plan cluster
    query_variants      TEXT[]  NOT NULL DEFAULT '{}',
    schema_types        TEXT[]  NOT NULL DEFAULT '{}',
    authority_signals   TEXT[]  NOT NULL DEFAULT '{}',
    content_brief       TEXT[]  NOT NULL DEFAULT '{}',

    -- Provenance
    model_used      TEXT        NOT NULL DEFAULT '',
    provider_used   TEXT        NOT NULL DEFAULT '',

    -- Lifecycle: draft → embedded → approved|rejected
    status          TEXT        NOT NULL DEFAULT 'draft',

    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One draft per (document, tenant, topic, intent) — reruns replace the old one
    CONSTRAINT uq_gap_content_drafts_doc_topic_intent
        UNIQUE (document_id, tenant_id, topic, intent)
);

CREATE INDEX IF NOT EXISTS idx_gap_content_drafts_document
    ON gap_content_drafts (document_id, tenant_id);

CREATE INDEX IF NOT EXISTS idx_gap_content_drafts_status
    ON gap_content_drafts (status, tenant_id);

CREATE INDEX IF NOT EXISTS idx_gap_content_drafts_priority
    ON gap_content_drafts (document_id, priority);
