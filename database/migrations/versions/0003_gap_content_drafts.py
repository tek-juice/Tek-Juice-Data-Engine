"""Add gap_content_drafts table

Revision ID: 0003_gap_content_drafts
Revises: 0002_gap_close_actions
Create Date: 2025-07-14

gap_content_drafts stores the LLM-generated content sections produced by
LLMWritingAgent for every intent cluster in a gap_close_actions plan.

Each row is one content section (one topic × one intent type).  The writing
agent upserts on (document_id, tenant_id, topic, intent) so reruns always
replace the previous draft with a fresher one.

Status lifecycle:
  draft     → created by the writing agent, not yet embedded
  embedded  → vectors stored; gap re-analysis will now see improved coverage
  approved  → (optional) human-reviewed and published to the CMS
  rejected  → (optional) human-rejected; agent will regenerate on next cycle
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_gap_content_drafts"
down_revision = "0002_gap_close_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gap_content_drafts",
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
        # The specific topic and intent this draft covers
        sa.Column("topic",  sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False,
                  comment="definitional|procedural|causal|comparative|quantitative|commercial|troubleshooting|authority"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="99"),
        # The generated content
        sa.Column(
            "draft_text",
            sa.Text(),
            nullable=False,
            comment="LLM-generated markdown content section",
        ),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        # Metadata from the closure plan cluster
        sa.Column(
            "query_variants",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Target search queries this section addresses",
        ),
        sa.Column(
            "schema_types",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
            comment="Schema.org types recommended for this section",
        ),
        sa.Column(
            "authority_signals",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "content_brief",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        # Provenance
        sa.Column("model_used",    sa.Text(), nullable=False, server_default=""),
        sa.Column("provider_used", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="draft",
            comment="draft | embedded | approved | rejected",
        ),
        sa.Column(
            "generated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
        # Upsert key: one draft per (document, tenant, topic, intent)
        sa.UniqueConstraint(
            "document_id",
            "tenant_id",
            "topic",
            "intent",
            name="uq_gap_content_drafts_doc_topic_intent",
        ),
    )

    op.create_index(
        "idx_gap_content_drafts_document",
        "gap_content_drafts",
        ["document_id", "tenant_id"],
    )
    op.create_index(
        "idx_gap_content_drafts_status",
        "gap_content_drafts",
        ["status", "tenant_id"],
    )
    op.create_index(
        "idx_gap_content_drafts_priority",
        "gap_content_drafts",
        ["document_id", "priority"],
    )


def downgrade() -> None:
    op.drop_index("idx_gap_content_drafts_priority",  "gap_content_drafts")
    op.drop_index("idx_gap_content_drafts_status",    "gap_content_drafts")
    op.drop_index("idx_gap_content_drafts_document",  "gap_content_drafts")
    op.drop_table("gap_content_drafts")
