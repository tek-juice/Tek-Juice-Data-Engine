"""
DATA ENGINE — Auth Router
Handles JWT token issuance, refresh, revocation, and API key management.
Phase 3: Central Command — API Manager.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.database import get_db_session
from configs.security import (
    verify_password, hash_password, create_access_token, create_refresh_token,
    decode_token, generate_api_key, hash_api_key,
)
from shared.authentication.jwt_handler import CurrentUser
from shared.exceptions.base import InvalidTokenError, UnauthorisedError

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["Authentication"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(default="", max_length=200)


class APIKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class APIKeyResponse(BaseModel):
    key_id: str
    api_key: str
    prefix: str
    name: str
    message: str = "Store this key securely — it will not be shown again."


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Register a new user under the default tenant and return tokens immediately."""
    # Check email not already taken
    existing = await db.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": body.email},
    )
    if existing.fetchone():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    # Resolve the default tenant (first active tenant in the system)
    tenant_row = await db.execute(
        text("SELECT id FROM tenants WHERE is_active = TRUE ORDER BY created_at LIMIT 1")
    )
    tenant = tenant_row.fetchone()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No active tenant found. Contact your administrator.",
        )

    import uuid
    user_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role)
            VALUES (:id, :tenant_id, :email, :hashed_password, :full_name, 'viewer')
        """),
        {
            "id":              user_id,
            "tenant_id":       str(tenant.id),
            "email":           body.email,
            "hashed_password": hash_password(body.password),
            "full_name":       body.full_name,
        },
    )
    logger.info("user_registered", user_id=user_id, email=body.email)

    from configs.settings import get_settings
    s = get_settings()
    access_token  = create_access_token(user_id, str(tenant.id), extra_claims={"role": "viewer"})
    refresh_token = create_refresh_token(user_id, str(tenant.id))
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=s.jwt_access_token_expire_minutes * 60,
    )


# ── Google OAuth 2.0 ──────────────────────────────────────────────────────────
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@router.get("/oauth/google")
async def google_oauth_initiate(
    request: Request,
    redirect_uri: str = Query(..., description="Frontend origin, e.g. https://app.example.com"),
):
    """
    Redirect the browser to Google's OAuth consent screen.

    Pass `redirect_uri` as the frontend origin (e.g. `https://app.example.com`).
    Google will call back to `<backend>/auth/oauth/google/callback`, which then
    redirects to `{redirect_uri}/auth/callback?access_token=...&refresh_token=...`.

    Register `<BACKEND_URL>/auth/oauth/google/callback` as an Authorised
    redirect URI in your Google Cloud Console project.
    """
    from configs.settings import get_settings
    import urllib.parse
    s = get_settings()

    if not s.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth is not configured on this instance. Use email/password login.",
        )

    # Encode the frontend origin in `state` so the callback knows where to redirect.
    state = urllib.parse.quote(redirect_uri.rstrip("/"), safe="")
    # Backend callback — must be registered in Google Cloud Console exactly as-is.
    backend_callback = str(request.base_url).rstrip("/") + "/auth/oauth/google/callback"

    auth_url = _GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id":     s.google_client_id,
        "redirect_uri":  backend_callback,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
        "prompt":        "select_account",
    })
    return RedirectResponse(auth_url)


@router.get("/oauth/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Google OAuth callback.  Exchanges `code` for tokens, upserts the user,
    issues Data Engine JWT tokens, and redirects to `{frontend_origin}/auth/callback`.
    """
    from configs.settings import get_settings
    import urllib.parse, uuid, httpx
    s = get_settings()

    if not s.google_client_id:
        raise HTTPException(status_code=501, detail="Google OAuth not configured.")

    # Recover the frontend origin from state.
    frontend_origin = urllib.parse.unquote(state).rstrip("/")
    backend_callback = str(request.base_url).rstrip("/") + "/auth/oauth/google/callback"

    # Exchange authorization code for tokens.
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code":          code,
                "client_id":     s.google_client_id,
                "client_secret": s.google_client_secret,
                "redirect_uri":  backend_callback,
                "grant_type":    "authorization_code",
            },
        )

    if token_resp.status_code != 200:
        logger.error("google_oauth_token_exchange_failed", status=token_resp.status_code)
        raise HTTPException(status_code=502, detail="Google token exchange failed.")

    google_tokens = token_resp.json()
    access_token_google = google_tokens.get("access_token")

    # Fetch user profile.
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token_google}"},
        )

    if userinfo_resp.status_code != 200:
        logger.error("google_oauth_userinfo_failed", status=userinfo_resp.status_code)
        raise HTTPException(status_code=502, detail="Failed to fetch Google user info.")

    userinfo = userinfo_resp.json()
    email     = userinfo.get("email")
    full_name = userinfo.get("name", "")

    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email address.")

    # Resolve or create the user.
    result = await db.execute(
        text("SELECT id, tenant_id, role, is_active FROM users WHERE email = :email"),
        {"email": email},
    )
    user = result.fetchone()

    if user:
        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account is inactive.")
        user_id    = str(user.id)
        tenant_id  = str(user.tenant_id)
        role       = user.role
    else:
        # Auto-register under the default tenant.
        tenant_row = await db.execute(
            text("SELECT id FROM tenants WHERE is_active = TRUE ORDER BY created_at LIMIT 1")
        )
        tenant = tenant_row.fetchone()
        if not tenant:
            raise HTTPException(status_code=503, detail="No active tenant found.")

        user_id   = str(uuid.uuid4())
        tenant_id = str(tenant.id)
        role      = "viewer"
        await db.execute(
            text("""
                INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role)
                VALUES (:id, :tenant_id, :email, '', :full_name, 'viewer')
            """),
            {"id": user_id, "tenant_id": tenant_id, "email": email, "full_name": full_name},
        )
        logger.info("google_oauth_user_created", user_id=user_id, email=email)

    await db.execute(
        text("UPDATE users SET last_login_at = NOW() WHERE id = :id"),
        {"id": user_id},
    )

    de_access_token  = create_access_token(user_id, tenant_id, extra_claims={"role": role})
    de_refresh_token = create_refresh_token(user_id, tenant_id)

    logger.info("google_oauth_login", user_id=user_id, email=email)

    # Redirect to frontend /auth/callback with tokens in query string.
    callback_url = (
        f"{frontend_origin}/auth/callback"
        f"?access_token={de_access_token}"
        f"&refresh_token={de_refresh_token}"
        f"&token_type=bearer"
        f"&expires_in={s.jwt_access_token_expire_minutes * 60}"
    )
    return RedirectResponse(callback_url)


@router.post("/token", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db_session),
):
    """Obtain JWT access and refresh tokens via username/password."""
    result = await db.execute(
        text("""
            SELECT u.id, u.hashed_password, u.role, u.is_active, u.tenant_id
            FROM users u
            WHERE u.email = :email
        """),
        {"email": form.username},
    )
    user = result.fetchone()

    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise UnauthorisedError("Account is inactive.")

    access_token = create_access_token(
        subject=str(user.id),
        tenant_id=str(user.tenant_id),
        extra_claims={"role": user.role},
    )
    refresh_token = create_refresh_token(
        subject=str(user.id),
        tenant_id=str(user.tenant_id),
    )

    await db.execute(
        text("UPDATE users SET last_login_at = NOW() WHERE id = :id"),
        {"id": str(user.id)},
    )
    logger.info("user_logged_in", user_id=str(user.id))

    from configs.settings import get_settings
    s = get_settings()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=s.jwt_access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest):
    """Exchange a refresh token for a new access token."""
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise InvalidTokenError("Not a refresh token.")
    except Exception as exc:
        raise InvalidTokenError(str(exc)) from exc

    access_token = create_access_token(
        subject=payload["sub"],
        tenant_id=payload["tenant_id"],
        extra_claims={"role": payload.get("role", "viewer")},
    )
    refresh_token_new = create_refresh_token(
        subject=payload["sub"],
        tenant_id=payload["tenant_id"],
    )
    from configs.settings import get_settings
    s = get_settings()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token_new,
        expires_in=s.jwt_access_token_expire_minutes * 60,
    )


@router.post("/api-keys", response_model=APIKeyResponse, status_code=201)
async def create_api_key(
    body: APIKeyCreateRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """Generate a new API key for the current tenant."""
    import uuid
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    prefix = raw_key[:12]
    key_id = str(uuid.uuid4())

    await db.execute(
        text("""
            INSERT INTO api_keys (id, tenant_id, key_hash, key_prefix, name)
            VALUES (:id, :tenant_id, :key_hash, :prefix, :name)
        """),
        {
            "id":        key_id,
            "tenant_id": current_user.tenant_id,
            "key_hash":  key_hash,
            "prefix":    prefix,
            "name":      body.name,
        },
    )
    logger.info("api_key_created", tenant_id=current_user.tenant_id, name=body.name)
    return APIKeyResponse(key_id=key_id, api_key=raw_key, prefix=prefix, name=body.name)


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """Revoke an API key."""
    await db.execute(
        text("""
            UPDATE api_keys SET is_active = false
            WHERE id = :key_id AND tenant_id = :tenant_id
        """),
        {"key_id": key_id, "tenant_id": current_user.tenant_id},
    )
    logger.info("api_key_revoked", key_id=key_id)


# ── Webhook Endpoint Management ───────────────────────────────────────────────

class WebhookCreateRequest(BaseModel):
    url: str = Field(..., description="HTTPS URL the Engine will POST events to")
    event_types: list[str] = Field(
        default=["*"],
        description='Events to subscribe to. Use ["*"] for all. '
                    'Options: document.completed, document.failed, gap.detected, '
                    'gap.resolved, drafts.ready, schema.generated, ranking.updated',
    )
    description: str = Field(default="", max_length=200)


class WebhookResponse(BaseModel):
    id: str
    url: str
    event_types: list[str]
    description: str
    is_active: bool
    created_at: str


@router.post("/webhooks", response_model=WebhookResponse, status_code=201,
             tags=["Authentication"])
async def register_webhook(
    body: WebhookCreateRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Register a webhook endpoint for your product.

    The Engine will POST a signed JSON payload to your URL whenever a
    subscribed event occurs.  Verify the signature using:

        import hmac, hashlib
        expected = hmac.new(YOUR_SECRET.encode(), request.body, hashlib.sha256).hexdigest()
        assert request.headers["X-DataEngine-Signature"] == f"sha256={expected}"

    The signing secret is returned **once** at registration — store it securely.
    """
    import uuid
    from configs.security import generate_api_key

    endpoint_id = str(uuid.uuid4())
    secret      = generate_api_key()   # 32-byte random secret for HMAC signing

    await db.execute(
        text("""
            INSERT INTO webhook_endpoints
                (id, tenant_id, url, secret, event_types, description, is_active)
            VALUES
                (:id, :tenant_id, :url, :secret, :event_types, :description, TRUE)
        """),
        {
            "id":          endpoint_id,
            "tenant_id":   current_user.tenant_id,
            "url":         body.url,
            "secret":      secret,
            "event_types": body.event_types,
            "description": body.description,
        },
    )
    logger.info(
        "webhook_registered",
        tenant_id=current_user.tenant_id,
        url=body.url,
        events=body.event_types,
    )
    return {
        "id":          endpoint_id,
        "url":         body.url,
        "event_types": body.event_types,
        "description": body.description,
        "is_active":   True,
        "secret":      secret,   # returned ONCE — store securely
        "created_at":  __import__("datetime").datetime.utcnow().isoformat(),
        "message":     "Store your secret securely — it will not be shown again.",
    }


@router.get("/webhooks", tags=["Authentication"])
async def list_webhooks(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """List all registered webhook endpoints for the current tenant."""
    result = await db.execute(
        text("""
            SELECT id, url, event_types, description, is_active,
                   created_at::text
            FROM webhook_endpoints
            WHERE tenant_id = :tenant_id
            ORDER BY created_at DESC
        """),
        {"tenant_id": current_user.tenant_id},
    )
    rows = [dict(r._mapping) for r in result.fetchall()]
    # Never return the secret in list responses
    return {"webhooks": rows, "count": len(rows)}


@router.delete("/webhooks/{endpoint_id}", status_code=204, tags=["Authentication"])
async def delete_webhook(
    endpoint_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """Deactivate (soft-delete) a webhook endpoint."""
    await db.execute(
        text("""
            UPDATE webhook_endpoints
            SET is_active = FALSE, updated_at = NOW()
            WHERE id = :endpoint_id AND tenant_id = :tenant_id
        """),
        {"endpoint_id": endpoint_id, "tenant_id": current_user.tenant_id},
    )
    logger.info("webhook_deleted", endpoint_id=endpoint_id)


@router.get("/webhooks/{endpoint_id}/logs", tags=["Authentication"])
async def webhook_delivery_logs(
    endpoint_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
    limit: int = 50,
):
    """
    Return the last N delivery attempts for a webhook endpoint.
    Useful for debugging missed or failed events.
    """
    result = await db.execute(
        text("""
            SELECT event_type, url, status_code, success,
                   error, duration_ms, attempted_at::text
            FROM webhook_delivery_log
            WHERE endpoint_id = :endpoint_id
              AND tenant_id   = :tenant_id
            ORDER BY attempted_at DESC
            LIMIT :limit
        """),
        {
            "endpoint_id": endpoint_id,
            "tenant_id":   current_user.tenant_id,
            "limit":       limit,
        },
    )
    rows = [dict(r._mapping) for r in result.fetchall()]
    return {
        "endpoint_id": endpoint_id,
        "logs":        rows,
        "count":       len(rows),
    }
