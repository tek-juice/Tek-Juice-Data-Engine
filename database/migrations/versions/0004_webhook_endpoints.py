"""Add webhook_endpoints and webhook_delivery_log tables

Revision ID: 0004_webhook_endpoints
Revises: 0003_gap_content_drafts
Create Date: 2025-07-14

webhook_endpoints
  One row per product callback URL.  A tenant may register multiple
  endpoints (e.g. one per environment, or one per event category).
  event_types is a text[] — products register for specific events
  or use ["*"] to receive everything.

webhook_delivery_log
  Immutable audit log of every delivery attempt.  Products can query
  their log via the API to debug missed events.
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_webhook_endpoints"
down_revision = "0003_gap_content_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── webhook_endpoints ─────────────────────────────────────────────────────
    op.create_table(
        "webhook_endpoints",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.UUID(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "url",
            sa.Text(),
            nullable=False,
            comment="HTTPS callback URL the Engine will POST to",
        ),
        sa.Column(
            "secret",
            sa.Text(),
            nullable=False,
            comment="HMAC-SHA256 signing secret — never returned in API responses",
        ),
        sa.Column(
            "event_types",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY['*']"),
            comment="Events this endpoint subscribes to. Use [\"*\"] for all.",
        ),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
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
    )

    op.create_index(
        "idx_webhook_endpoints_tenant",
        "webhook_endpoints",
        ["tenant_id", "is_active"],
    )

    # ── webhook_delivery_log ──────────────────────────────────────────────────
    op.create_table(
        "webhook_delivery_log",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "endpoint_id",
            sa.UUID(),
            nullable=False,
            comment="References webhook_endpoints.id (no FK — log persists after endpoint deletion)",
        ),
        sa.Column("tenant_id",   sa.UUID(),    nullable=False),
        sa.Column("event_type",  sa.Text(),    nullable=False),
        sa.Column("url",         sa.Text(),    nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column(
            "success",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("error",       sa.Text(),    nullable=True),
        sa.Column(
            "duration_ms",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "attempted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "idx_webhook_delivery_log_tenant",
        "webhook_delivery_log",
        ["tenant_id", "attempted_at"],
    )
    op.create_index(
        "idx_webhook_delivery_log_endpoint",
        "webhook_delivery_log",
        ["endpoint_id"],
    )
    op.create_index(
        "idx_webhook_delivery_log_success",
        "webhook_delivery_log",
        ["tenant_id", "success"],
    )


def downgrade() -> None:
    op.drop_index("idx_webhook_delivery_log_success",  "webhook_delivery_log")
    op.drop_index("idx_webhook_delivery_log_endpoint", "webhook_delivery_log")
    op.drop_index("idx_webhook_delivery_log_tenant",   "webhook_delivery_log")
    op.drop_table("webhook_delivery_log")
    op.drop_index("idx_webhook_endpoints_tenant", "webhook_endpoints")
    op.drop_table("webhook_endpoints")
