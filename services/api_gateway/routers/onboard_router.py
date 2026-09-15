"""
DATA ENGINE — Onboarding Router
Self-service product connection API — no authentication required.
Protection is provided by email verification + injection bridge test.

Routes:
    POST /onboard                  — submit the connect wizard form
    GET  /onboard/verify-email     — click link from verification email
    POST /onboard/scan             — detect website architecture
    GET  /onboard/ping             — confirm injection bridge is live
    POST /onboard/install          — install injection bridge after credentials provided
"""

import secrets
import uuid
from datetime import datetime, UTC
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
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
router = APIRouter(tags=["Onboarding"])


# ── Request / Response models ─────────────────────────────────────────────────

class OnboardRequest(BaseModel):
    product_name: str  = Field(..., min_length=2,  max_length=200)
    website_url:  str  = Field(..., min_length=8,  max_length=500)
    admin_email:  EmailStr


class ScanRequest(BaseModel):
    website_url: str = Field(..., min_length=8, max_length=500)


class InstallRequest(BaseModel):
    tenant_id:   str
    platform:    str
    credentials: dict[str, Any]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug_from_name(name: str) -> str:
    """Convert a product name to a URL-safe slug."""
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")[:80]
    return slug + "-" + secrets.token_hex(3)


# ── POST /onboard ─────────────────────────────────────────────────────────────

@router.post("/onboard", status_code=202)
async def submit_onboard(
    body: OnboardRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Step 1 of the wizard — receive the form submission.
    Creates tenant + user + API key, sends verification email.
    Returns immediately (202) — activation happens via email link.
    """
    # ── Duplicate check ───────────────────────────────────────────────────────
    existing = await db.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": str(body.admin_email)},
    )
    if existing.fetchone():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists. Check your inbox for the verification link, or contact support.",
        )

    # ── Create tenant ─────────────────────────────────────────────────────────
    tenant_id         = str(uuid.uuid4())
    verification_token= secrets.token_urlsafe(32)
    website_url       = str(body.website_url).rstrip("/")
    slug              = _slug_from_name(body.product_name)

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
            "id":         tenant_id,
            "name":       body.product_name,
            "slug":       slug,
            "website_url":website_url,
            "token":      verification_token,
            "meta":       '{"source": "self_service_wizard"}',
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
        # Don't fail the request — the user can resend from the wizard

    logger.info("onboard_submitted", tenant_id=tenant_id, email=str(body.admin_email))

    return {
        "status":       "verification_sent",
        "tenant_id":    tenant_id,
        "api_key":      raw_key,          # shown in wizard — not stored plaintext again
        "message":      "Check your inbox and click the verification link to continue.",
        "email":        str(body.admin_email),
    }


# ── GET /onboard/verify-email ─────────────────────────────────────────────────

@router.get("/onboard/verify-email", response_class=HTMLResponse)
async def verify_email(
    token: str = Query(...),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Clicked from the verification email.
    Activates the tenant, advances the wizard to Step 2 (architecture scan).
    Returns an HTML page — the wizard continues in the browser.
    """
    result = await db.execute(
        text("""
            SELECT t.id, t.name, t.website_url, t.email_verified
            FROM tenants t
            WHERE t.verification_token = :token
        """),
        {"token": token},
    )
    row = result.fetchone()

    if not row:
        return HTMLResponse(
            _wizard_error_page("Invalid or expired verification link. Please start again at the connect page."),
            status_code=400,
        )

    if row.email_verified:
        # Already verified — redirect straight to step 2
        pass
    else:
        # Mark verified + clear token
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

    s = get_settings()
    # Return the wizard page at Step 2 — architecture scan
    return HTMLResponse(
        _wizard_step2_page(
            tenant_id   = str(row.id),
            product_name= row.name,
            website_url = row.website_url or "",
            engine_url  = s.engine_public_url,
        )
    )


# ── POST /onboard/scan ────────────────────────────────────────────────────────

@router.post("/onboard/scan")
async def scan_architecture(body: ScanRequest):
    """
    Scan a website and return the detected architecture + credential guide.
    Called by the wizard after email verification.
    """
    result: DetectionResult = await detect(body.website_url)
    return {
        "platform_type":    result.platform_type,
        "display_name":     result.display_name,
        "confidence":       result.confidence,
        "signals":          result.signals,
        "inject_strategy":  result.inject_strategy,
        "credential_hint":  result.credential_hint,
        "credential_guide": result.credential_guide,
    }


# ── POST /onboard/install ─────────────────────────────────────────────────────

@router.post("/onboard/install")
async def install_bridge(
    body: InstallRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Step 3 — receive credentials, test connection, install injection bridge.
    Encrypts and stores credentials, fires first crawl on success.

    SSH-based installs are dispatched as a background Celery task so the
    HTTP response returns immediately — the browser polls /onboard/ping
    to confirm when the install completes.
    """
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
        raise HTTPException(status_code=403, detail="Email not verified. Please check your inbox.")

    # Validate required fields are not empty
    empty_fields = [k for k, v in body.credentials.items() if not str(v).strip()]
    if empty_fields:
        return {
            "success": False,
            "error":   f"These fields are required: {', '.join(empty_fields)}",
            "hint":    "Please fill in all fields and try again.",
        }

    # Get the API key for this tenant (needed for SSH receivers)
    key_result = await db.execute(
        text("SELECT key_prefix FROM api_keys WHERE tenant_id = :tid LIMIT 1"),
        {"tid": body.tenant_id},
    )
    key_row    = key_result.fetchone()
    api_key_prefix = key_row.key_prefix if key_row else "de_unknown"

    creds = body.credentials

    # ── SSH-based platforms: test SSH creds now, install via background task ──
    is_ssh = body.platform in (
        "ssh_custom", "nextjs", "laravel", "django", "express", "unknown"
    )
    if is_ssh:
        from services.onboarding.injectors.ssh_injector import SSHInjector
        creds["api_key"] = api_key_prefix
        injector  = SSHInjector(creds)
        ssh_test  = await injector.test_connection()
        if not ssh_test.get("success"):
            return {
                "success": False,
                "error":   ssh_test.get("error", "SSH connection failed."),
                "hint":    "Check your server hostname, username, and password then try again.",
            }
        # SSH connection verified — dispatch full install as background task
        # so the browser does not time out waiting for it
        from workers.celery.tasks.injection_tasks import install_ssh_bridge
        install_ssh_bridge.delay(body.tenant_id, body.platform, creds)

        # Mark status as 'configuring' so the UI can show a progress state
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

        return {
            "success":    True,
            "installing": True,
            "message":    "SSH verified. Installing the receiver on your server — this takes about 30 seconds. The page will confirm when it is live.",
            "platform":   body.platform,
        }

    # ── API-based platforms: test + confirm immediately ───────────────────────
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
        return {
            "success": False,
            "error":   test_result.get("error", "Connection failed."),
            "hint":    "Please double-check the credentials and try again.",
        }

    # ── Store encrypted credentials + update tenant ───────────────────────────
    encrypted = encrypt_credentials(creds)
    injection_config = {
        "platform":     body.platform,
        "installed_at": datetime.now(UTC).isoformat(),
        "details":      {
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
            "config":   _json.dumps(injection_config),   # safe JSON — no Python quotes
            "platform": body.platform,
            "id":       body.tenant_id,
        },
    )
    await db.commit()

    # ── Fire first crawl ──────────────────────────────────────────────────────
    try:
        from workers.celery.tasks.ingestion_tasks import crawl_and_ingest_website
        crawl_and_ingest_website.delay(body.tenant_id)
        logger.info("first_crawl_queued", tenant_id=body.tenant_id)
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

    return {
        "success":    True,
        "installing": False,
        "message":    "Your product is connected. The engine is running.",
        "platform":   body.platform,
        "details":    injection_config["details"],
    }


# ── GET /onboard/ping ─────────────────────────────────────────────────────────

@router.get("/onboard/ping")
async def ping_connection(
    tenant_id: str | None = Query(None, description="Tenant ID (used by the wizard SSH polling)"),
    api_key:   str | None = Query(None, description="API key (legacy / manual check)"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Poll injection status.  Accepts either:
      • ?tenant_id=<uuid>   — used by the in-browser SSH install polling loop
      • ?api_key=<key>      — used for manual / external connection checks
    Returns injection_status so the wizard knows when the SSH install is done.
    """
    if not tenant_id and not api_key:
        raise HTTPException(
            status_code=400,
            detail="Provide either tenant_id or api_key as a query parameter.",
        )

    if tenant_id:
        result = await db.execute(
            text("""
                SELECT name, injection_status, onboarding_completed_at
                FROM tenants
                WHERE id = :id
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
        return {"connected": False, "reason": "Tenant or API key not found."}

    return {
        "connected":               True,
        "product":                 row.name,
        "injection_status":        row.injection_status,
        "onboarding_completed_at": row.onboarding_completed_at.isoformat()
                                   if row.onboarding_completed_at else None,
    }


# ── Wizard HTML helpers ───────────────────────────────────────────────────────

def _wizard_error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Error — Data Engine</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:520px;margin:80px auto;padding:0 24px;color:#1f2328}}
h2{{color:#e11d48}}p{{color:#57606a}}a{{color:#3b82d4}}</style></head>
<body>
<h2>Something went wrong</h2>
<p>{message}</p>
<p><a href="/connect">← Start again</a></p>
</body></html>"""


def _wizard_step2_page(tenant_id: str, product_name: str, website_url: str, engine_url: str) -> str:
    """
    Step 2 + Step 3 page — shown after email link is clicked.
    Matches the same 3-dot step indicator from the Step 1 connect page.
    Step 2 = scanning + credential entry.
    Step 3 = connecting / installing.
    Step 4 = done (success screen).
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect {product_name} — Tek Juice Data Engine</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"Segoe UI",system-ui,sans-serif;font-size:15px;
  background:#f7f8fa;color:#1f2328;min-height:100vh;display:flex;
  align-items:center;justify-content:center;padding:24px}}
.wrap{{width:100%;max-width:540px}}
.brand{{text-align:center;margin-bottom:24px}}
.brand-name{{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#3b82d4;margin-bottom:3px}}
.brand-tag{{font-size:12px;color:#57606a}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:36px}}

/* ── Step dots (matches Step 1 page) ── */
.steps{{display:flex;align-items:center;gap:0;margin-bottom:28px}}
.dot{{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-size:12px;font-weight:700;flex-shrink:0;transition:all .3s}}
.dot.done{{background:#16a34a;color:#fff}}
.dot.active{{background:#3b82d4;color:#fff}}
.dot.inactive{{background:#e5e7eb;color:#57606a}}
.line{{flex:1;height:2px;background:#e5e7eb;transition:background .3s}}
.line.done{{background:#16a34a}}

/* ── Typography ── */
h2{{font-size:20px;font-weight:700;margin-bottom:6px}}
.sub{{color:#57606a;font-size:14px;margin-bottom:24px;line-height:1.5}}

/* ── Scan states ── */
.state-row{{display:flex;align-items:center;gap:10px;padding:14px 16px;
  border-radius:8px;margin-bottom:16px;font-size:14px}}
.state-scanning{{background:#f7f8fa;color:#57606a;border:1px solid #e5e7eb}}
.state-detected{{background:#f0fdf4;color:#15803d;border:1px solid #bbf7d0}}
.state-unknown{{background:#fffbeb;color:#92400e;border:1px solid #fde68a}}
.spinner{{width:16px;height:16px;border:2px solid currentColor;border-top-color:transparent;
  border-radius:50%;animation:spin .7s linear infinite;flex-shrink:0;opacity:.5}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.state-icon{{font-size:16px;flex-shrink:0}}

/* ── Guide box ── */
.guide{{background:#f7f8fa;border:1px solid #e5e7eb;border-radius:8px;
  padding:16px;margin-bottom:20px;display:none}}
.guide-title{{font-size:13px;font-weight:700;color:#1f2328;margin-bottom:12px}}
.guide-step{{display:flex;gap:10px;margin-bottom:8px;align-items:flex-start}}
.guide-num{{width:20px;height:20px;border-radius:50%;background:#3b82d4;color:#fff;
  font-size:10px;font-weight:700;display:flex;align-items:center;
  justify-content:center;flex-shrink:0;margin-top:1px}}
.guide-text{{font-size:13px;color:#57606a;line-height:1.5}}

/* ── Form fields ── */
.field{{margin-bottom:14px}}
label{{display:block;font-size:12px;font-weight:700;color:#57606a;
  text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px}}
input,textarea{{width:100%;border:1px solid #e5e7eb;border-radius:7px;
  padding:10px 13px;font-size:14px;color:#1f2328;outline:none;
  transition:border-color .15s,box-shadow .15s;background:#fff;
  font-family:inherit}}
input:focus,textarea:focus{{border-color:#3b82d4;box-shadow:0 0 0 3px rgba(59,130,212,.1)}}
input.invalid{{border-color:#e11d48!important}}
input::placeholder,textarea::placeholder{{color:#adb5bd}}

/* ── Buttons ── */
.btn{{width:100%;padding:13px;background:#3b82d4;color:#fff;border:none;
  border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;
  margin-top:4px;transition:opacity .15s;display:flex;
  align-items:center;justify-content:center;gap:8px}}
.btn:hover:not(:disabled){{opacity:.9}}
.btn:disabled{{opacity:.55;cursor:not-allowed}}
.btn-spinner{{width:16px;height:16px;border:2.5px solid rgba(255,255,255,.4);
  border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;display:none}}

/* ── Error / info ── */
.err-box{{background:#fff1f2;border:1px solid #fecdd3;color:#be123c;
  padding:12px 14px;border-radius:8px;margin-top:12px;font-size:13px;
  display:none;line-height:1.5}}
.info-box{{background:#eff6ff;border:1px solid #bfdbfe;color:#1d4ed8;
  padding:14px;border-radius:8px;margin-bottom:20px;font-size:13px;line-height:1.6}}

/* ── SSH installing progress ── */
.installing-box{{text-align:center;padding:28px 16px;display:none}}
.installing-icon{{font-size:40px;margin-bottom:12px}}
.installing-box h3{{font-size:17px;font-weight:700;margin-bottom:8px}}
.installing-box p{{color:#57606a;font-size:14px;margin-bottom:4px;line-height:1.5}}
.progress-bar{{width:100%;height:6px;background:#e5e7eb;border-radius:3px;
  margin-top:16px;overflow:hidden}}
.progress-fill{{height:100%;background:#3b82d4;border-radius:3px;
  animation:progress 30s linear forwards}}
@keyframes progress{{from{{width:0%}}to{{width:95%}}}}

/* ── Success screen ── */
.success-screen{{text-align:center;display:none;padding:8px 0}}
.success-icon{{font-size:48px;margin-bottom:14px}}
.success-screen h2{{color:#16a34a;font-size:20px;margin-bottom:8px}}
.success-screen p{{color:#57606a;font-size:14px;margin-bottom:5px;line-height:1.5}}
.check-list{{text-align:left;margin:16px 0;padding:16px;
  background:#f0fdf4;border-radius:8px;border:1px solid #bbf7d0}}
.check-item{{display:flex;gap:8px;font-size:13px;color:#15803d;margin-bottom:5px}}
.check-item:last-child{{margin-bottom:0}}

/* ── Footer ── */
.footer{{text-align:center;margin-top:16px;font-size:12px;color:#adb5bd}}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">
    <div class="brand-name">Tek Juice Data Engine</div>
    <div class="brand-tag">AI-powered search dominance</div>
  </div>

  <div class="card">
    <!-- Step dots — matches Step 1 page exactly -->
    <div class="steps">
      <div class="dot done"   id="d1">✓</div>
      <div class="line done"  id="l1"></div>
      <div class="dot done"   id="d2">✓</div>
      <div class="line"       id="l2"></div>
      <div class="dot active" id="d3">3</div>
      <div class="line"       id="l3"></div>
      <div class="dot inactive" id="d4">4</div>
    </div>

    <!-- ── Panel A: Scanning ── -->
    <div id="panel-scan">
      <h2>Scanning your website…</h2>
      <p class="sub">We're detecting your platform so we know how to connect. This takes a few seconds.</p>
      <div class="state-row state-scanning" id="scan-status">
        <div class="spinner"></div>
        <span>Scanning <strong>{website_url}</strong>…</span>
      </div>
    </div>

    <!-- ── Panel B: Credential entry (shown after scan) ── -->
    <div id="panel-creds" style="display:none">
      <h2 id="creds-title">Connect your platform</h2>
      <p class="sub" id="creds-sub">Follow the steps below to get your access key, then paste it in.</p>

      <div class="state-row state-detected" id="detected-row" style="display:none">
        <span class="state-icon">✅</span>
        <span id="detected-label"></span>
      </div>
      <div class="state-row state-unknown" id="unknown-row" style="display:none">
        <span class="state-icon">⚠️</span>
        <span>We couldn't automatically detect your platform. That's fine — just provide SSH access and we'll figure it out on the server.</span>
      </div>

      <div class="guide" id="guide-box">
        <div class="guide-title" id="guide-title"></div>
        <div id="guide-steps"></div>
      </div>

      <div id="dynamic-fields"></div>

      <button class="btn" id="connect-btn" onclick="installBridge()">
        <span id="btn-text">Connect →</span>
        <div class="btn-spinner" id="btn-spinner"></div>
      </button>
      <div class="err-box" id="err-box"></div>
    </div>

    <!-- ── Panel C: SSH installing (background task) ── -->
    <div id="panel-installing" class="installing-box">
      <div class="installing-icon">⚙️</div>
      <h3>Installing on your server…</h3>
      <p>We're setting up the Data Engine receiver on your server.</p>
      <p>This takes about 30 seconds. Please don't close this page.</p>
      <div class="progress-bar"><div class="progress-fill"></div></div>
    </div>

    <!-- ── Panel D: Success ── -->
    <div id="panel-success" class="success-screen">
      <div class="success-icon">✅</div>
      <h2>{product_name} is connected!</h2>
      <p>The Data Engine is now running for your product.</p>
      <div class="check-list">
        <div class="check-item"><span>✅</span><span>Your website is being crawled right now</span></div>
        <div class="check-item"><span>✅</span><span>Content gaps will be detected within minutes</span></div>
        <div class="check-item"><span>✅</span><span>AI-generated content will be published automatically</span></div>
        <div class="check-item"><span>✅</span><span>Auto-crawl runs every 24 hours — no action needed</span></div>
      </div>
      <p style="margin-top:12px;font-size:13px;color:#57606a;">
        Check your email — we've sent you a confirmation with your dashboard link.
      </p>
    </div>
  </div>

  <div class="footer">Tek Juice Data Engine</div>
</div>

<script>
const TENANT_ID = "{tenant_id}";
const WEBSITE   = "{website_url}";
const ENGINE    = "{engine_url}";
let detectedPlatform = null;
let pollTimer = null;

// ── Show a panel, hide all others ─────────────────────────────────────────
function showPanel(id) {{
  ['panel-scan','panel-creds','panel-installing','panel-success']
    .forEach(p => document.getElementById(p).style.display = p === id ? 'block' : 'none');
  if (id === 'panel-installing') document.getElementById('panel-installing').style.display = 'block';
  if (id === 'panel-success') document.getElementById('panel-success').style.display = 'block';
}}

// ── Update step dots ───────────────────────────────────────────────────────
function setStep(active) {{
  for (let i = 1; i <= 4; i++) {{
    const dot  = document.getElementById('d' + i);
    const line = document.getElementById('l' + i);
    if (i < active) {{
      dot.className  = 'dot done';
      dot.textContent = '✓';
      if (line) line.className = 'line done';
    }} else if (i === active) {{
      dot.className  = 'dot active';
      dot.textContent = String(i);
      if (line) line.className = 'line';
    }} else {{
      dot.className  = 'dot inactive';
      dot.textContent = String(i);
      if (line) line.className = 'line';
    }}
  }}
}}

// ── Step 3: scan on page load ─────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {{
  setStep(3);
  showPanel('panel-scan');
  try {{
    const r = await fetch(ENGINE + '/onboard/scan', {{
      method:  'POST',
      headers: {{'Content-Type':'application/json'}},
      body:    JSON.stringify({{website_url: WEBSITE}}),
    }});
    if (!r.ok) throw new Error('Scan request failed');
    const d = await r.json();
    detectedPlatform = d.inject_strategy;
    renderDetection(d);
    renderGuide(d);
    renderFields(d.inject_strategy);
    setStep(3);
    showPanel('panel-creds');
  }} catch(e) {{
    // Scan failed — fall back to SSH / unknown
    detectedPlatform = 'unknown';
    renderDetection(null);
    renderGuide({{
      inject_strategy:  'unknown',
      credential_hint:  'Server SSH Credentials',
      credential_guide: [
        {{step:'1', instruction:'We could not reach your website to scan it. That\'s okay — provide SSH access and we\'ll detect your platform on the server directly.'}},
        {{step:'2', instruction:'Enter your server hostname or IP address below.'}},
        {{step:'3', instruction:'Enter your SSH username (usually ubuntu, root, or your username).'}},
        {{step:'4', instruction:'Enter your SSH password or paste your private key.'}},
      ]
    }});
    renderFields('unknown');
    setStep(3);
    showPanel('panel-creds');
  }}
}});

// ── Render detection result ───────────────────────────────────────────────
function renderDetection(d) {{
  const detRow = document.getElementById('detected-row');
  const unkRow = document.getElementById('unknown-row');
  if (d && d.confidence > 0.3) {{
    detRow.style.display = 'flex';
    unkRow.style.display = 'none';
    document.getElementById('detected-label').innerHTML =
      '<strong>' + d.display_name + ' detected</strong>' +
      (d.signals && d.signals.length
        ? ' &nbsp;·&nbsp; <span style="font-size:12px;opacity:.7">' + d.signals.slice(0,2).join(' · ') + '</span>'
        : '');
    document.getElementById('creds-title').textContent = 'Connect ' + d.display_name;
    document.getElementById('creds-sub').textContent =
      'Follow the steps below to get your ' + d.credential_hint + ', then paste it in.';
  }} else {{
    detRow.style.display = 'none';
    unkRow.style.display = 'flex';
    document.getElementById('creds-title').textContent = 'Connect your server';
    document.getElementById('creds-sub').textContent =
      'Provide SSH access and we\'ll detect and install everything automatically.';
  }}
}}

// ── Render step-by-step guide ─────────────────────────────────────────────
function renderGuide(d) {{
  if (!d || !d.credential_guide || !d.credential_guide.length) return;
  const box   = document.getElementById('guide-box');
  const title = document.getElementById('guide-title');
  const steps = document.getElementById('guide-steps');
  box.style.display  = 'block';
  title.textContent  = 'How to get your ' + d.credential_hint + ':';
  steps.innerHTML    = '';
  d.credential_guide.forEach(s => {{
    const div = document.createElement('div');
    div.className = 'guide-step';
    div.innerHTML =
      '<div class="guide-num">' + s.step + '</div>' +
      '<div class="guide-text">' + s.instruction + '</div>';
    steps.appendChild(div);
  }});
}}

// ── Render credential input fields ────────────────────────────────────────
function renderFields(strategy) {{
  const el = document.getElementById('dynamic-fields');
  const f  = (id, label, ph, type) => type === 'textarea'
    ? `<div class="field"><label for="${{id}}">${{label}}</label>
       <textarea id="${{id}}" rows="4" placeholder="${{ph}}" style="resize:vertical"></textarea></div>`
    : `<div class="field"><label for="${{id}}">${{label}}</label>
       <input id="${{id}}" type="${{type || 'text'}}" placeholder="${{ph}}" autocomplete="off"/></div>`;

  if (strategy === 'wordpress') {{
    el.innerHTML =
      f('wp_url',  'Your WordPress Website URL',  'https://your-site.com') +
      f('wp_user', 'WordPress Username',           'admin or your username') +
      f('wp_pass', 'Application Password',         'xxxx xxxx xxxx xxxx xxxx xxxx', 'password');

  }} else if (strategy === 'shopify') {{
    el.innerHTML =
      f('sh_domain', 'Shopify Store Domain',       'your-store.myshopify.com') +
      f('sh_token',  'Admin API Access Token',     'shpat_xxxxxxxxxxxxxxxxxxxxxxxx', 'password');

  }} else if (strategy === 'wix') {{
    el.innerHTML =
      f('wix_site', 'Wix Site ID',  'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx') +
      f('wix_key',  'Wix API Key',  'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', 'password');

  }} else if (strategy === 'webflow') {{
    el.innerHTML =
      f('wf_token', 'Webflow API Token', 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx', 'password');

  }} else if (strategy === 'graphql') {{
    el.innerHTML =
      f('gql_url',   'GraphQL Endpoint URL', 'https://your-site.com/graphql') +
      f('gql_token', 'Auth Token',            'Bearer xxxxxxxx...', 'password');

  }} else {{
    // SSH — all custom/unknown backends
    el.innerHTML =
      f('ssh_host', 'Server Hostname or IP Address', 'e.g. 12.34.56.78 or myserver.com') +
      f('ssh_user', 'SSH Username',                   'ubuntu') +
      f('ssh_pass', 'SSH Password',                   'Leave blank if using a private key', 'password') +
      f('ssh_key',  'SSH Private Key  (optional)',    '-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----', 'textarea');
  }}
}}

// ── Collect values from fields ────────────────────────────────────────────
function collectCreds() {{
  const s = detectedPlatform;
  const v = id => (document.getElementById(id) || {{}}).value || '';
  if (s === 'wordpress') return {{ site_url: v('wp_url'), username: v('wp_user'), app_password: v('wp_pass') }};
  if (s === 'shopify')   return {{ shop_domain: v('sh_domain'), access_token: v('sh_token') }};
  if (s === 'wix')       return {{ site_id: v('wix_site'), api_key: v('wix_key') }};
  if (s === 'webflow')   return {{ api_token: v('wf_token') }};
  if (s === 'graphql')   return {{ endpoint: v('gql_url'), auth_token: v('gql_token') }};
  return {{ host: v('ssh_host'), username: v('ssh_user'), password: v('ssh_pass'), private_key: v('ssh_key') }};
}}

// ── Validate fields before submitting ────────────────────────────────────
function validateCreds(creds) {{
  const s = detectedPlatform;
  const required = {{
    wordpress: ['site_url','username','app_password'],
    shopify:   ['shop_domain','access_token'],
    wix:       ['site_id','api_key'],
    webflow:   ['api_token'],
    graphql:   ['endpoint','auth_token'],
  }}[s] || ['host','username'];

  const missing = [];
  const idMap = {{
    site_url:'wp_url', username:'wp_user', app_password:'wp_pass',
    shop_domain:'sh_domain', access_token:'sh_token',
    site_id:'wix_site', api_key:'wix_key', api_token:'wf_token',
    endpoint:'gql_url', auth_token:'gql_token',
    host:'ssh_host',
  }};

  required.forEach(key => {{
    const val = creds[key] || '';
    if (!val.trim()) {{
      missing.push(key.replace(/_/g,' '));
      const inputId = idMap[key] || key;
      const el = document.getElementById(inputId);
      if (el) el.classList.add('invalid');
    }}
  }});
  return missing;
}}

// ── Install bridge ────────────────────────────────────────────────────────
async function installBridge() {{
  // Clear previous errors and highlights
  document.querySelectorAll('input.invalid').forEach(el => el.classList.remove('invalid'));
  const err = document.getElementById('err-box');
  err.style.display = 'none';

  const creds = collectCreds();
  const missing = validateCreds(creds);
  if (missing.length) {{
    err.innerHTML = '⚠️ Please fill in: <strong>' + missing.join(', ') + '</strong>';
    err.style.display = 'block';
    return;
  }}

  // Show spinner on button
  const btn     = document.getElementById('connect-btn');
  const spinner = document.getElementById('btn-spinner');
  const btnText = document.getElementById('btn-text');
  btn.disabled        = true;
  spinner.style.display = 'block';
  btnText.textContent   = 'Connecting…';

  try {{
    const r = await fetch(ENGINE + '/onboard/install', {{
      method:  'POST',
      headers: {{'Content-Type':'application/json'}},
      body:    JSON.stringify({{
        tenant_id:   TENANT_ID,
        platform:    detectedPlatform,
        credentials: creds,
      }}),
    }});
    const d = await r.json();

    if (!d.success) {{
      err.innerHTML = '❌ <strong>' + (d.error || 'Connection failed.') + '</strong>'
        + (d.hint ? '<br><span style="opacity:.75">' + d.hint + '</span>' : '');
      err.style.display = 'block';
      btn.disabled        = false;
      spinner.style.display = 'none';
      btnText.textContent   = 'Try Again →';
      return;
    }}

    if (d.installing) {{
      // SSH background install — show progress panel and poll
      setStep(4);
      showPanel('panel-installing');
      startPolling();
    }} else {{
      // API-based — connected immediately
      setStep(4);
      showPanel('panel-success');
    }}

  }} catch(e) {{
    err.innerHTML = '❌ Network error. Please check your internet connection and try again.';
    err.style.display = 'block';
    btn.disabled        = false;
    spinner.style.display = 'none';
    btnText.textContent   = 'Try Again →';
  }}
}}

// ── Poll /onboard/ping until injection_status = live or failed ────────────
function startPolling() {{
  let attempts = 0;
  pollTimer = setInterval(async () => {{
    attempts++;
    try {{
      const r = await fetch(ENGINE + '/onboard/ping?tenant_id=' + TENANT_ID);
      const d = await r.json();
      if (d.injection_status === 'live') {{
        clearInterval(pollTimer);
        showPanel('panel-success');
      }} else if (d.injection_status === 'failed' || attempts > 20) {{
        clearInterval(pollTimer);
        showPanel('panel-creds');
        setStep(3);
        const err = document.getElementById('err-box');
        err.innerHTML = '❌ Installation failed on your server. Please check your SSH credentials and try again.';
        err.style.display = 'block';
        const btn = document.getElementById('connect-btn');
        const spinner = document.getElementById('btn-spinner');
        const btnText = document.getElementById('btn-text');
        btn.disabled = false;
        spinner.style.display = 'none';
        btnText.textContent = 'Try Again →';
      }}
    }} catch(e) {{}}
  }}, 4000);  // poll every 4 seconds
}}
</script>
</body>
</html>"""
