"""Add onboarding columns to tenants table

Revision ID: 0006_onboarding
Revises: 0005_tenant_website_url
Create Date: 2025-07-16

Adds columns required by the self-service onboarding system:

  email_verified          — TRUE once the owner clicks the verification link.
                            Unverified tenants cannot use the engine.

  verification_token      — One-time token emailed to the owner. Cleared on use.

  platform_type           — Detected architecture: wordpress, shopify, wix,
                            webflow, nextjs, laravel, django, express, graphql,
                            headless_cms, unknown.

  injection_config        — JSONB storing non-secret injection settings:
                            endpoint URL, content type, publish strategy, etc.

  injection_credentials   — AES-256 encrypted JSON blob storing the credential
                            the owner provided (App Password, API key, SSH creds).
                            Never stored in plaintext.

  injection_status        — current state: pending | configured | live | failed

  onboarding_completed_at — Timestamp of when the owner finished the wizard.
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_onboarding"
down_revision = "0005_tenant_website_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="TRUE once the owner verifies their email address",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "verification_token",
            sa.Text(),
            nullable=True,
            comment="One-time email verification token — cleared after use",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "platform_type",
            sa.Text(),
            nullable=True,
            comment="Detected website platform: wordpress|shopify|wix|webflow|nextjs|laravel|django|express|graphql|headless_cms|unknown",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "injection_config",
            sa.JSON(),
            nullable=True,
            comment="Non-secret injection settings: endpoint URL, content type, publish strategy",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "injection_credentials",
            sa.Text(),
            nullable=True,
            comment="AES-256 encrypted JSON blob of the owner-provided credential — never plaintext",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "injection_status",
            sa.Text(),
            nullable=False,
            server_default="'pending'",
            comment="Injection bridge state: pending|configuring|live|failed",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "onboarding_completed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Timestamp when the owner completed the onboarding wizard",
        ),
    )

    op.create_index(
        "idx_tenants_injection_status",
        "tenants",
        ["injection_status"],
        postgresql_where=sa.text("injection_status != 'live'"),
    )
    op.create_index(
        "idx_tenants_verification_token",
        "tenants",
        ["verification_token"],
        postgresql_where=sa.text("verification_token IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_tenants_verification_token", "tenants")
    op.drop_index("idx_tenants_injection_status",   "tenants")
    op.drop_column("tenants", "onboarding_completed_at")
    op.drop_column("tenants", "injection_status")
    op.drop_column("tenants", "injection_credentials")
    op.drop_column("tenants", "injection_config")
    op.drop_column("tenants", "platform_type")
    op.drop_column("tenants", "verification_token")
    op.drop_column("tenants", "email_verified")
