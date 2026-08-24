"""Add gap_close_actions table

Revision ID: 0002_gap_close_actions
Revises: 0001_initial
Create Date: 2025-07-14

gap_close_actions stores the machine-readable closure plan produced by
GapOptimiser.auto_close_gap() for every document that has an open content
gap.  The content layer reads this table to know exactly what sections to
write, in what order, and with what schema types to rank first.

Columns
-------
document_id     – FK to documents
tenant_id       – FK to tenants (RLS guard)
gap_score       – gap score at the time the plan was created
severity        – low | medium | high | critical
missing_topics  – array of topic strings from GapAnalyzer
close_plan      – full ContentClusterReport as JSONB
status          – pending | resolved
created_at      – when this plan was first written
updated_at      – last upsert timestamp
resolved_at     – set when gap_score drops below LOW threshold after re-analysis

Unique constraint on (document_id, tenant_id) — one live plan per document;
upserted each time gap analysis runs so the plan always reflects the current
gap state.
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_gap_close_actions"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gap_close_actions",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id",
            sa.UUID(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.UUID(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gap_score",      sa.Float(),   nullable=False),
        sa.Column("severity",       sa.Text(),    nullable=False),
        sa.Column(
            "missing_topics",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "close_plan",
            sa.JSON(),
            nullable=False,
            server_default="{}",
            comment="Full ContentClusterReport JSON — intent clusters, briefs, schema types",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
            comment="pending | resolved",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "resolved_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Set when gap_score drops below LOW threshold after re-analysis",
        ),
        sa.UniqueConstraint(
            "document_id",
            "tenant_id",
            name="uq_gap_close_actions_doc_tenant",
        ),
    )

    # Speed up the batch query that sweeps all pending close actions
    op.create_index(
        "idx_gap_close_actions_status",
        "gap_close_actions",
        ["status", "tenant_id"],
    )
    op.create_index(
        "idx_gap_close_actions_severity",
        "gap_close_actions",
        ["severity", "gap_score"],
    )
    op.create_index(
        "idx_gap_close_actions_document",
        "gap_close_actions",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_gap_close_actions_document", "gap_close_actions")
    op.drop_index("idx_gap_close_actions_severity", "gap_close_actions")
    op.drop_index("idx_gap_close_actions_status",   "gap_close_actions")
    op.drop_table("gap_close_actions")
