-- DATA ENGINE — Migration 006: SERP Rank Tracking & Domain Authority
-- Adds tables for tracking keyword rankings and domain backlink authority.

-- ── Rank Tracking Config
-- Stores the (domain, keyword, location) combinations to track.

CREATE TABLE IF NOT EXISTS rank_tracking_config (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    domain          TEXT NOT NULL,
    keyword         TEXT NOT NULL,
    location_code   INTEGER NOT NULL DEFAULT 2840,  -- 2840 = USA
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    tags            TEXT[] DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, domain, keyword, location_code)
);

CREATE INDEX IF NOT EXISTS idx_rtc_tenant_id  ON rank_tracking_config(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rtc_domain     ON rank_tracking_config(domain);
CREATE INDEX IF NOT EXISTS idx_rtc_is_active  ON rank_tracking_config(is_active);

CREATE TRIGGER trg_rtc_updated_at
    BEFORE UPDATE ON rank_tracking_config
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── Rank Tracking Snapshots
-- Daily SERP position snapshots per (domain, keyword, location).

CREATE TABLE IF NOT EXISTS rank_tracking (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
    domain          TEXT NOT NULL,
    keyword         TEXT NOT NULL,
    location_code   INTEGER NOT NULL DEFAULT 2840,
    position        INTEGER,                        -- NULL = not in top-100
    ranking_url     TEXT,
    search_volume   INTEGER,
    cpc             NUMERIC(10, 4),
    competition     NUMERIC(5, 4),
    snapshot_date   DATE NOT NULL DEFAULT CURRENT_DATE,
    raw_data        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (domain, keyword, location_code, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_rt_domain        ON rank_tracking(domain);
CREATE INDEX IF NOT EXISTS idx_rt_keyword       ON rank_tracking(keyword);
CREATE INDEX IF NOT EXISTS idx_rt_snapshot_date ON rank_tracking(snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_rt_position      ON rank_tracking(position) WHERE position IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rt_tenant_id     ON rank_tracking(tenant_id);


-- ── Domain Authority Snapshots
-- Daily authority and backlink metric snapshots per domain.

CREATE TABLE IF NOT EXISTS domain_authority (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID REFERENCES tenants(id) ON DELETE CASCADE,
    domain              TEXT NOT NULL,
    domain_rank         INTEGER,                    -- 0–100, DataForSEO Domain Rank
    total_backlinks     BIGINT,
    referring_domains   INTEGER,
    dofollow_backlinks  BIGINT,
    nofollow_backlinks  BIGINT,
    spam_score          NUMERIC(5, 4),              -- 0.0–1.0
    new_backlinks_30d   INTEGER,
    lost_backlinks_30d  INTEGER,
    top_anchors         TEXT[] DEFAULT '{}',
    snapshot_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    raw_data            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (domain, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_da_domain        ON domain_authority(domain);
CREATE INDEX IF NOT EXISTS idx_da_tenant_id     ON domain_authority(tenant_id);
CREATE INDEX IF NOT EXISTS idx_da_snapshot_date ON domain_authority(snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_da_domain_rank   ON domain_authority(domain_rank DESC) WHERE domain_rank IS NOT NULL;
