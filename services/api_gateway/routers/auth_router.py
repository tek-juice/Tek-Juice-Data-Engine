"""
DATA ENGINE — Auth Router
Handles JWT token issuance, refresh, revocation, and API key management.
Phase 3: Central Command — API Manager.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.database import get_db_session
from configs.security import (
    verify_password, create_access_token, create_refresh_token,
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


class APIKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)


class APIKeyResponse(BaseModel):
    key_id: str
    api_key: str
    prefix: str
    name: str
    message: str = "Store this key securely — it will not be shown again."


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
