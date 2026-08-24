"""Add website_url and crawl_config to tenants table

Revision ID: 0005_tenant_website_url
Revises: 0004_webhook_endpoints
Create Date: 2025-07-14

Adds two columns to tenants:

  website_url   — the company's root URL the Engine will auto-crawl
                  (e.g. https://acme.com).  NULL until the tenant registers it.

  crawl_config  — JSONB for optional per-tenant crawl settings:
                  max_pages (default 50), max_depth (default 3),
                  recrawl_interval_hours (default 24).

Once website_url is set, the daily Celery beat task
'crawl-tenant-websites-daily' will automatically crawl all pages,
ingest each one through the full pipeline (chunk → embed → gap → write),
and keep the tenant's content perpetually up-to-date with zero human effort.
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_tenant_website_url"
down_revision = "0004_webhook_endpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "website_url",
            sa.Text(),
            nullable=True,
            comment="Root URL for automated site crawl (e.g. https://acme.com)",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "crawl_config",
            sa.JSON(),
            nullable=False,
            server_default='{"max_pages": 50, "max_depth": 3, "recrawl_interval_hours": 24}',
            comment="Per-tenant crawl settings — max_pages, max_depth, recrawl_interval_hours",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "last_crawled_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Timestamp of last successful automated site crawl",
        ),
    )
    op.create_index(
        "idx_tenants_website_url",
        "tenants",
        ["website_url"],
        postgresql_where=sa.text("website_url IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_tenants_website_url", "tenants")
    op.drop_column("tenants", "last_crawled_at")
    op.drop_column("tenants", "crawl_config")
    op.drop_column("tenants", "website_url")
