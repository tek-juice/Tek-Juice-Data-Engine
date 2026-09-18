-- DATA ENGINE — Migration 007: add missing columns to scraped_trends
-- These columns exist in 002_telemetry_schema.sql but the live table
-- was created before they were added.

ALTER TABLE scraped_trends
    ADD COLUMN IF NOT EXISTS title           TEXT,
    ADD COLUMN IF NOT EXISTS url             TEXT,
    ADD COLUMN IF NOT EXISTS snippet         TEXT,
    ADD COLUMN IF NOT EXISTS published_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS raw_content     TEXT,
    ADD COLUMN IF NOT EXISTS relevance_score FLOAT;
