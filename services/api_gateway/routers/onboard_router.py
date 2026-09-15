"""
DATA ENGINE — Onboarding Router
Self-service product connection API — no authentication required.
Protection is provided by email verification + injection bridge test.

All endpoints return JSON. The frontend handles all UI/UX.

Routes:
    POST /onboard                  — register a new product (sends verification email)
    POST /onboard/verify-email     — verify email token, returns tenant_id + api_key
    POST /onboard/scan             — detect website platform/architecture
    POST /onboard/install          — install injection bridge with provided credentials
    GET  /onboard/ping             — poll injection status (SSH install progress)
"""

import secrets
import uuid
from datetime import datetime, UTC
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.database import get_db_session
from configs.security import generate_api_key, hash_api_key
from configs.settings import get_settings
from services.onboarding.architecture_detector import detect, DetectionResult
from services.onboarding.email_service import (
    send_verification_email,
    send_onboarding_complete_email,
)
from services.onboarding.credential_store import encrypt_credentials

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/onboard", tags=["Onboarding"])


# ── Request / Response models ─────────────────────────────────────────────────

class OnboardRequest(BaseModel):
    """Register a new product with the Data Engine."""
    product_name: str      = Field(..., min_length=2,  max_length=200, example="Acme Tourism Kenya")
    website_url:  str      = Field(..., min_length=8,  max_length=500, example="https://acmetourism.co.ke")
    admin_email:  EmailStr = Field(..., example="admin@acmetourism.co.ke")


class OnboardResponse(BaseModel):
    status:     str
    tenant_id:  str
    api_key:    str
    message:    str
    email:      str


class VerifyEmailRequest(BaseModel):
    """Verify the email token sent to the product owner."""
    token: str = Field(..., description="Verification token from the email link")


class VerifyEmailResponse(BaseModel):
    status:      str
    tenant_id:   str
    product_name: str
    website_url:  str
    message:      str


class ScanRequest(BaseModel):
    """Scan a website to detect its platform and get credential instructions."""
    website_url: str = Field(..., min_length=8, max_length=500, example="https://acmetourism.co.ke")


class ScanResponse(BaseModel):
    platform_type:    str
    display_name:     str
    confidence:       float
    signals:          list[str]
    inject_strategy:  str
    credential_hint:  str
    credential_guide: list[dict]


class InstallRequest(BaseModel):
    """Install the injection bridge after the owner provides credentials."""
    tenant_id:   str            = Field(..., description="Tenant ID returned from /onboard")
    platform:    str            = Field(..., description="Platform type from /onboard/scan (e.g. wordpress, shopify, ssh_custom)")
    credentials: dict[str, Any] = Field(..., description="Platform credentials — keys depend on platform type")


class InstallResponse(BaseModel):
    success:    bool
    installing: bool = False
    message:    str
    platform:   str  = ""
    error:      str  = ""
    hint:       str  = ""


class PingResponse(BaseModel):
    connected:               bool
    product:                 str  = ""
    injection_status:        str  = ""
    onboarding_completed_at: str | None = None
    reason:                  str  = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug_from_name(name: str) -> str:
    """Convert a product name to a URL-safe slug."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")[:80]
    return slug + "-" + secrets.token_hex(3)


# ── POST /onboard ─────────────────────────────────────────────────────────────

@router.post(
    "",
    status_code=202,
    response_model=OnboardResponse,
    summary="Register a new product",
    description="""
Submit a new product for onboarding. Creates a tenant, generates an API key,
and sends a verification email to the admin.

The returned `api_key` is shown **once** — store it securely.
Activation happens when the owner clicks the link in the verification email
and calls `POST /onboard/verify-email` with the token.
""",
)
async def submit_onboard(
    body: OnboardRequest,
    db: AsyncSession = Depends(get_db_session),
):
    # ── Duplicate check ───────────────────────────────────────────────────────
    existing = await db.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": str(body.admin_email)},
    )
    if existing.fetchone():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    # ── Create tenant ─────────────────────────────────────────────────────────
    tenant_id          = str(uuid.uuid4())
    verification_token = secrets.token_urlsafe(32)
    website_url        = str(body.website_url).rstrip("/")
    slug               = _slug_from_name(body.product_name)

    await db.execute(
        text("""
            INSERT INTO tenants
                (id, name, slug, tier, is_active, website_url,
                 email_verified, verification_token, injection_status, metadata)
            VALUES
                (:id, :name, :slug, 'standard', true, :website_url,
                 false, :token, 'pending', :meta::jsonb)
        """),
        {
            "id":          tenant_id,
            "name":        body.product_name,
            "slug":        slug,
            "website_url": website_url,
            "token":       verification_token,
            "meta":        '{"source": "self_service_api"}',
        },
    )

    # ── Create admin user ─────────────────────────────────────────────────────
    user_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role)
            VALUES (:id, :tenant_id, :email, '', :full_name, 'admin')
        """),
        {
            "id":        user_id,
            "tenant_id": tenant_id,
            "email":     str(body.admin_email),
            "full_name": body.product_name,
        },
    )

    # ── Generate API key ──────────────────────────────────────────────────────
    raw_key  = generate_api_key()
    key_hash = hash_api_key(raw_key)
    prefix   = raw_key[:12]
    key_id   = str(uuid.uuid4())

    await db.execute(
        text("""
            INSERT INTO api_keys (id, tenant_id, key_hash, key_prefix, name)
            VALUES (:id, :tenant_id, :key_hash, :prefix, :name)
        """),
        {
            "id":        key_id,
            "tenant_id": tenant_id,
            "key_hash":  key_hash,
            "prefix":    prefix,
            "name":      f"{body.product_name} — auto-generated",
        },
    )

    await db.commit()

    # ── Send verification email ───────────────────────────────────────────────
    try:
        send_verification_email(
            to=str(body.admin_email),
            product_name=body.product_name,
            token=verification_token,
        )
    except Exception as exc:
        logger.error("verification_email_failed", email=str(body.admin_email), error=str(exc))

    logger.info("onboard_submitted", tenant_id=tenant_id, email=str(body.admin_email))

    return OnboardResponse(
        status    = "verification_sent",
        tenant_id = tenant_id,
        api_key   = raw_key,
        message   = "Verification email sent. Call POST /onboard/verify-email with the token from the email to activate.",
        email     = str(body.admin_email),
    )


# ── POST /onboard/verify-email ────────────────────────────────────────────────

@router.post(
    "/verify-email",
    response_model=VerifyEmailResponse,
    summary="Verify email token",
    description="""
Called by the frontend after the user clicks the verification link in their email.
The link contains a `token` query param — extract it and POST it here.

On success, the tenant is activated and the frontend can proceed to
`POST /onboard/scan` to detect the platform.
""",
)
async def verify_email(
    body: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        text("""
            SELECT t.id, t.name, t.website_url, t.email_verified
            FROM tenants t
            WHERE t.verification_token = :token
        """),
        {"token": body.token},
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired verification token.",
        )

    if not row.email_verified:
        await db.execute(
            text("""
                UPDATE tenants
                SET email_verified = true, verification_token = NULL
                WHERE id = :id
            """),
            {"id": str(row.id)},
        )
        await db.commit()

    logger.info("email_verified", tenant_id=str(row.id))

    return VerifyEmailResponse(
        status       = "verified",
        tenant_id    = str(row.id),
        product_name = row.name,
        website_url  = row.website_url or "",
        message      = "Email verified. Proceed to POST /onboard/scan to detect the platform.",
    )


# ── POST /onboard/scan ────────────────────────────────────────────────────────

@router.post(
    "/scan",
    response_model=ScanResponse,
    summary="Detect website platform",
    description="""
Scans the product's website and detects the platform (WordPress, Shopify,
Wix, Webflow, Next.js, Laravel, Django, Express, GraphQL, etc.).

Returns the detected platform, confidence score, and a step-by-step
`credential_guide` the frontend should display to the user so they know
exactly which credentials to provide for `POST /onboard/install`.
""",
)
async def scan_architecture(body: ScanRequest):
    result: DetectionResult = await detect(body.website_url)
    return ScanResponse(
        platform_type    = result.platform_type,
        display_name     = result.display_name,
        confidence       = result.confidence,
        signals          = result.signals,
        inject_strategy  = result.inject_strategy,
        credential_hint  = result.credential_hint,
        credential_guide = result.credential_guide,
    )


# ── POST /onboard/install ─────────────────────────────────────────────────────

@router.post(
    "/install",
    response_model=InstallResponse,
    summary="Install injection bridge",
    description="""
Validates the provided credentials and installs the injection bridge.

**API-based platforms** (WordPress, Shopify, Wix, Webflow, GraphQL):
Tests the connection immediately and returns `success: true` when live.

**SSH-based platforms** (Next.js, Laravel, Django, Express, custom):
Tests SSH access, then dispatches the full install as a background task.
Returns `installing: true` — poll `GET /onboard/ping?tenant_id=` every
4 seconds until `injection_status` is `live`.

**Credential keys by platform:**

| platform | required keys |
|---|---|
| `wordpress` | `site_url`, `username`, `app_password` |
| `shopify` | `shop_domain`, `access_token` |
| `wix` | `site_id`, `api_key` |
| `webflow` | `api_token` |
| `graphql` | `endpoint`, `auth_token` |
| `ssh_custom` / `nextjs` / `laravel` / `django` / `express` | `host`, `username`, `password` or `private_key` |
""",
)
async def install_bridge(
    body: InstallRequest,
    db: AsyncSession = Depends(get_db_session),
):
    import json as _json

    # Verify tenant exists and is email-verified
    result = await db.execute(
        text("SELECT id, name, website_url, email_verified FROM tenants WHERE id = :id"),
        {"id": body.tenant_id},
    )
    tenant = result.fetchone()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    if not tenant.email_verified:
        raise HTTPException(status_code=403, detail="Email not verified. Call POST /onboard/verify-email first.")

    # Validate required fields are not empty
    empty_fields = [k for k, v in body.credentials.items() if not str(v).strip()]
    if empty_fields:
        return InstallResponse(
            success = False,
            message = "",
            error   = f"These fields are required: {', '.join(empty_fields)}",
            hint    = "Please fill in all required credential fields.",
        )

    # Get the API key prefix for this tenant (needed for SSH receivers)
    key_result = await db.execute(
        text("SELECT key_prefix FROM api_keys WHERE tenant_id = :tid LIMIT 1"),
        {"tid": body.tenant_id},
    )
    key_row        = key_result.fetchone()
    api_key_prefix = key_row.key_prefix if key_row else "de_unknown"

    creds = body.credentials

    # ── SSH-based platforms ───────────────────────────────────────────────────
    is_ssh = body.platform in (
        "ssh_custom", "nextjs", "laravel", "django", "express", "unknown"
    )
    if is_ssh:
        from services.onboarding.injectors.ssh_injector import SSHInjector
        creds["api_key"] = api_key_prefix
        injector  = SSHInjector(creds)
        ssh_test  = await injector.test_connection()
        if not ssh_test.get("success"):
            return InstallResponse(
                success = False,
                message = "",
                error   = ssh_test.get("error", "SSH connection failed."),
                hint    = "Check your server hostname, username, and password then try again.",
            )
        # Dispatch full install as background task — returns immediately
        from workers.celery.tasks.injection_tasks import install_ssh_bridge
        install_ssh_bridge.delay(body.tenant_id, body.platform, creds)

        await db.execute(
            text("""
                UPDATE tenants
                SET injection_status = 'configuring',
                    platform_type    = :platform
                WHERE id = :id
            """),
            {"platform": body.platform, "id": body.tenant_id},
        )
        await db.commit()

        return InstallResponse(
            success    = True,
            installing = True,
            message    = "SSH verified. Installing receiver on your server (~30 seconds). Poll GET /onboard/ping?tenant_id= until injection_status is live.",
            platform   = body.platform,
        )

    # ── API-based platforms ───────────────────────────────────────────────────
    test_result: dict[str, Any] = {"success": False, "error": "Unknown platform"}

    if body.platform == "wordpress":
        from services.onboarding.injectors.wordpress import WordPressInjector
        test_result = await WordPressInjector(creds).test_connection()
    elif body.platform == "shopify":
        from services.onboarding.injectors.shopify import ShopifyInjector
        test_result = await ShopifyInjector(creds).test_connection()
    elif body.platform == "wix":
        from services.onboarding.injectors.wix import WixInjector
        test_result = await WixInjector(creds).test_connection()
    elif body.platform == "webflow":
        from services.onboarding.injectors.webflow import WebflowInjector
        test_result = await WebflowInjector(creds).test_connection()
    elif body.platform == "graphql":
        from services.onboarding.injectors.graphql_injector import GraphQLInjector
        test_result = await GraphQLInjector(creds).test_connection()

    if not test_result.get("success"):
        return InstallResponse(
            success = False,
            message = "",
            error   = test_result.get("error", "Connection failed."),
            hint    = "Please double-check the credentials and try again.",
        )

    # ── Store encrypted credentials + update tenant ───────────────────────────
    encrypted        = encrypt_credentials(creds)
    injection_config = {
        "platform":     body.platform,
        "installed_at": datetime.now(UTC).isoformat(),
        "details": {
            k: v for k, v in test_result.items()
            if k != "success" and isinstance(v, (str, int, float, bool, type(None)))
        },
    }

    await db.execute(
        text("""
            UPDATE tenants
            SET injection_credentials   = :creds,
                injection_config        = :config::jsonb,
                injection_status        = 'live',
                platform_type           = :platform,
                onboarding_completed_at = NOW()
            WHERE id = :id
        """),
        {
            "creds":    encrypted,
            "config":   _json.dumps(injection_config),
            "platform": body.platform,
            "id":       body.tenant_id,
        },
    )
    await db.commit()

    # ── Fire first crawl ──────────────────────────────────────────────────────
    try:
        from workers.celery.tasks.ingestion_tasks import crawl_and_ingest_website
        crawl_and_ingest_website.delay(body.tenant_id)
    except Exception as exc:
        logger.error("first_crawl_dispatch_failed", tenant_id=body.tenant_id, error=str(exc))

    # ── Send completion email ─────────────────────────────────────────────────
    try:
        user_result = await db.execute(
            text("SELECT email FROM users WHERE tenant_id = :tid LIMIT 1"),
            {"tid": body.tenant_id},
        )
        user_row = user_result.fetchone()
        if user_row:
            s = get_settings()
            send_onboarding_complete_email(
                to           = user_row.email,
                product_name = tenant.name,
                dashboard_url= f"{s.engine_public_url}/dashboard",
            )
    except Exception as exc:
        logger.error("completion_email_failed", error=str(exc))

    logger.info("onboarding_complete", tenant_id=body.tenant_id, platform=body.platform)

    return InstallResponse(
        success    = True,
        installing = False,
        message    = "Your product is connected. The engine is running.",
        platform   = body.platform,
    )


# ── GET /onboard/ping ─────────────────────────────────────────────────────────

@router.get(
    "/ping",
    response_model=PingResponse,
    summary="Poll injection status",
    description="""
Poll the injection status for a tenant. Use this to check when an SSH-based
install has completed.

- `injection_status: configuring` — install is still running, keep polling
- `injection_status: live`        — connected and ready
- `injection_status: failed`      — install failed, ask user to retry

Accepts either `tenant_id` (for SSH polling) or `api_key` (for manual checks).
""",
)
async def ping_connection(
    tenant_id: str | None = Query(None, description="Tenant UUID — use this during SSH install polling"),
    api_key:   str | None = Query(None, description="API key — use this for manual connection checks"),
    db: AsyncSession = Depends(get_db_session),
):
    if not tenant_id and not api_key:
        raise HTTPException(
            status_code=400,
            detail="Provide either tenant_id or api_key as a query parameter.",
        )

    if tenant_id:
        result = await db.execute(
            text("""
                SELECT name, injection_status, onboarding_completed_at
                FROM tenants WHERE id = :id
            """),
            {"id": tenant_id},
        )
    else:
        key_hash = hash_api_key(api_key)
        result = await db.execute(
            text("""
                SELECT t.name, t.injection_status, t.onboarding_completed_at
                FROM api_keys ak
                JOIN tenants t ON t.id = ak.tenant_id
                WHERE ak.key_hash = :key_hash AND ak.is_active = true
            """),
            {"key_hash": key_hash},
        )

    row = result.fetchone()
    if not row:
        return PingResponse(connected=False, reason="Tenant or API key not found.")

    return PingResponse(
        connected               = True,
        product                 = row.name,
        injection_status        = row.injection_status,
        onboarding_completed_at = row.onboarding_completed_at.isoformat()
                                  if row.onboarding_completed_at else None,
    )
