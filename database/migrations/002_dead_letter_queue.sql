-- DATA ENGINE — Migration 002: Dead-Letter Queue & Scraper Alerts
-- Stores raw failed scraper payloads for manual inspection.
-- Raises dashboard alerts when scraper schema validation fails.

BEGIN;

-- ── Dead-Letter Queue ─────────────────────────────────────────────────────────
-- When a platform API changes its payload shape, failed items land here
-- instead of halting the Celery worker pipeline.
CREATE TABLE IF NOT EXISTS scraper_dead_letter_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        TEXT NOT NULL,              -- e.g. twitter, tiktok, facebook
    source          TEXT NOT NULL,              -- e.g. social_media, google, bing
    query           TEXT,                       -- search query that triggered the scrape
    raw_payload     JSONB NOT NULL,             -- the unparsed raw JSON from the API
    error_type      TEXT NOT NULL,              -- ValidationError, HTTPError, etc.
    error_detail    TEXT NOT NULL,              -- full error message / field path
    api_status_code INTEGER,                    -- HTTP status if applicable
    retry_count     INTEGER NOT NULL DEFAULT 0,
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dlq_platform   ON scraper_dead_letter_queue(platform);
CREATE INDEX IF NOT EXISTS idx_dlq_resolved   ON scraper_dead_letter_queue(resolved);
CREATE INDEX IF NOT EXISTS idx_dlq_created_at ON scraper_dead_letter_queue(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dlq_error_type ON scraper_dead_letter_queue(error_type);

-- ── Scraper Health Alerts ─────────────────────────────────────────────────────
-- Tracks per-platform scraper health. Dashboard reads this table.
CREATE TABLE IF NOT EXISTS scraper_health (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        TEXT NOT NULL UNIQUE,       -- one row per platform
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    total_failures  INTEGER NOT NULL DEFAULT 0,
    total_successes INTEGER NOT NULL DEFAULT 0,
    last_error_type TEXT,
    last_error_msg  TEXT,
    alert_fired     BOOLEAN NOT NULL DEFAULT FALSE,
    alert_fired_at  TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Insert default rows for all platforms
INSERT INTO scraper_health (platform) VALUES
    ('hackernews'), ('reddit'), ('twitter'), ('facebook'),
    ('instagram'), ('tiktok'), ('snapchat'), ('youtube'), ('linkedin'),
    ('google'), ('bing'), ('news')
ON CONFLICT (platform) DO NOTHING;

COMMIT;
