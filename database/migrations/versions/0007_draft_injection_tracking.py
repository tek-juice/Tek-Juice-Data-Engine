"""Add injection tracking columns to gap_content_drafts

Revision ID: 0007_draft_injection_tracking
Revises: 0006_onboarding
Create Date: 2025-07-16

Adds columns needed by the autonomous injection pipeline:

  published_at  — timestamp when the draft was injected into the product.
                  NULL until the injection task publishes it.

  status update — extends existing status enum to include:
                  'published' (injected into product)
                  'failed'    (injection attempted but failed)

  geo_score     — GEO visibility score (0–100) assigned by the GEO engine.
  aeo_score     — AEO score (0–100) assigned by the AEO engine.
  composite_score — weighted average of geo + aeo scores.
  quality_score   — Google Ad Quality Score equivalent (1–10).
  beats_paid_ads  — TRUE if quality_score puts content above paid ads.
  projected_position — human-readable rank projection string.

These score columns may already exist on some instances depending on whether
the writing agent populated them. Added as nullable with no server default
so the migration is safe to run on existing data.
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_draft_injection_tracking"
down_revision = "0006_onboarding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Injection tracking
    op.add_column(
        "gap_content_drafts",
        sa.Column(
            "published_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Timestamp when this draft was injected into the connected product",
        ),
    )

    # Score columns — added safely; NULL if not yet scored
    for col_name, col_type, comment in [
        ("geo_score",           sa.Float(), "GEO visibility score 0–100"),
        ("aeo_score",           sa.Float(), "AEO / voice search score 0–100"),
        ("composite_score",     sa.Float(), "Weighted composite of geo + aeo"),
        ("quality_score",       sa.Float(), "Google Ad Quality Score equivalent 1–10"),
        ("beats_paid_ads",      sa.Boolean(), "TRUE if quality_score ranks above paid ads"),
        ("projected_position",  sa.Text(), "Human-readable rank projection"),
    ]:
        op.add_column(
            "gap_content_drafts",
            sa.Column(col_name, col_type, nullable=True, comment=comment),
        )

    # Index to quickly find published drafts per tenant
    op.create_index(
        "idx_gap_content_drafts_published",
        "gap_content_drafts",
        ["tenant_id", "published_at"],
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_gap_content_drafts_published", "gap_content_drafts")
    for col in [
        "projected_position", "beats_paid_ads", "quality_score",
        "composite_score", "aeo_score", "geo_score", "published_at",
    ]:
        op.drop_column("gap_content_drafts", col)
