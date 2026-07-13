"""
DATA ENGINE — Auth Router
Handles JWT token issuance, refresh, revocation, and API key management.
Phase 3: Central Command — API Manager.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
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
