"""Replace DataForSEO SEO structures with SearXNG visibility tracking."""

from alembic import op


revision = "0008_searxng_seo"
down_revision = "0007_draft_injection_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The legacy SEO tables are empty, so rebuild them around SearXNG.
    op.drop_table("domain_authority")
    op.drop_table("rank_tracking")
    op.drop_table("rank_tracking_config")

    op.execute("""
        CREATE TABLE rank_tracking_config (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            domain          TEXT NOT NULL,
            keyword         TEXT NOT NULL,
            language        TEXT NOT NULL DEFAULT 'en',
            tags            TEXT[] DEFAULT '{}',
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, domain, keyword, language)
        )
    """)

    op.execute("""
        CREATE INDEX idx_rtc_tenant_id
            ON rank_tracking_config(tenant_id)
    """)

    op.execute("""
        CREATE INDEX idx_rtc_domain
            ON rank_tracking_config(domain)
    """)

    op.execute("""
        CREATE INDEX idx_rtc_is_active
            ON rank_tracking_config(is_active)
    """)

    op.execute("""
        CREATE TABLE rank_tracking (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
            domain          TEXT NOT NULL,
            keyword         TEXT NOT NULL,
            language        TEXT NOT NULL DEFAULT 'en',
            position        INTEGER,
            ranking_url     TEXT,
            title           TEXT,
            snippet         TEXT,
            engines         TEXT[] DEFAULT '{}',
            score           NUMERIC,
            rank_found      BOOLEAN NOT NULL DEFAULT FALSE,
            results_checked INTEGER NOT NULL DEFAULT 0,
            snapshot_date   DATE NOT NULL DEFAULT CURRENT_DATE,
            raw_data        JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, domain, keyword, language, snapshot_date)
        )
    """)

    op.execute("""
        CREATE INDEX idx_rt_domain
            ON rank_tracking(domain)
    """)

    op.execute("""
        CREATE INDEX idx_rt_keyword
            ON rank_tracking(keyword)
    """)

    op.execute("""
        CREATE INDEX idx_rt_snapshot_date
            ON rank_tracking(snapshot_date DESC)
    """)

    op.execute("""
        CREATE INDEX idx_rt_position
            ON rank_tracking(position)
            WHERE position IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX idx_rt_tenant_id
            ON rank_tracking(tenant_id)
    """)

    op.execute("""
        CREATE TABLE domain_visibility (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID REFERENCES tenants(id) ON DELETE CASCADE,
            domain          TEXT NOT NULL,
            queries_checked INTEGER NOT NULL DEFAULT 0,
            queries_found   INTEGER NOT NULL DEFAULT 0,
            top_3_count     INTEGER NOT NULL DEFAULT 0,
            top_10_count    INTEGER NOT NULL DEFAULT 0,
            top_20_count    INTEGER NOT NULL DEFAULT 0,
            average_position NUMERIC,
            visibility_rate NUMERIC,
            snapshot_date   DATE NOT NULL DEFAULT CURRENT_DATE,
            raw_data        JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (tenant_id, domain, snapshot_date)
        )
    """)

    op.execute("""
        CREATE INDEX idx_dv_domain
            ON domain_visibility(domain)
    """)

    op.execute("""
        CREATE INDEX idx_dv_tenant_id
            ON domain_visibility(tenant_id)
    """)

    op.execute("""
        CREATE INDEX idx_dv_snapshot_date
            ON domain_visibility(snapshot_date DESC)
    """)


def downgrade() -> None:
    op.drop_table("domain_visibility")
    op.drop_table("rank_tracking")
    op.drop_table("rank_tracking_config")
