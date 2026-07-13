"""
DATA ENGINE — JWT Authentication Handler
FastAPI dependencies for JWT and API key authentication.
"""

import hashlib
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from configs.database import get_db_session
from configs.security import decode_token
from configs.constants import ErrorCode
from shared.exceptions.base import InvalidTokenError, InvalidAPIKeyError, ForbiddenError

logger = structlog.get_logger(__name__)

# ── Security Schemes ──────────────────────────────────────────────────────────

bearer_scheme = HTTPBearer(auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


# ── Token Payload ─────────────────────────────────────────────────────────────

class TokenPayload:
    """Decoded JWT payload passed to route handlers."""

    def __init__(self, sub: str, tenant_id: str, role: str = "viewer") -> None:
        self.sub = sub
        self.tenant_id = tenant_id
        self.role = role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# ── JWT Dependency ────────────────────────────────────────────────────────────

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    api_key: Annotated[str | None, Security(api_key_scheme)],
    db: AsyncSession = Depends(get_db_session),
) -> TokenPayload:
    """
    FastAPI dependency — resolves the authenticated user from:
    1. Bearer JWT token
    2. X-API-Key header
    Raises 401 if neither is valid.
    """
    if credentials:
        return await _resolve_jwt(credentials.credentials)

    if api_key:
        return await _resolve_api_key(api_key, db)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _resolve_jwt(token: str) -> TokenPayload:
    """Decode and validate a JWT token."""
    try:
        payload = decode_token(token)
        sub = payload.get("sub")
        tenant_id = payload.get("tenant_id")
        token_type = payload.get("type", "access")

        if not sub or not tenant_id:
            raise InvalidTokenError("Token is missing required claims.")
        if token_type != "access":
            raise InvalidTokenError("Refresh tokens cannot be used for API access.")

        return TokenPayload(sub=sub, tenant_id=tenant_id, role=payload.get("role", "viewer"))

    except JWTError as exc:
        raise InvalidTokenError(str(exc)) from exc


async def _resolve_api_key(api_key: str, db: AsyncSession) -> TokenPayload:
    """Look up an API key and return the associated tenant context."""
    from sqlalchemy import text

    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    result = await db.execute(
        text("""
            SELECT ak.tenant_id, ak.is_active, t.is_active AS tenant_active
            FROM api_keys ak
            JOIN tenants t ON t.id = ak.tenant_id
            WHERE ak.key_hash = :key_hash
        """),
        {"key_hash": key_hash},
    )
    row = result.fetchone()

    if not row:
        raise InvalidAPIKeyError()
    if not row.is_active:
        raise InvalidAPIKeyError("API key has been revoked.")
    if not row.tenant_active:
        raise ForbiddenError("Tenant account is inactive.")

    # Update last_used_at asynchronously
    await db.execute(
        text("UPDATE api_keys SET last_used_at = NOW() WHERE key_hash = :key_hash"),
        {"key_hash": key_hash},
    )

    return TokenPayload(sub="api_key", tenant_id=str(row.tenant_id))


# ── Role-Based Access Shortcuts ───────────────────────────────────────────────

async def require_admin(
    current_user: Annotated[TokenPayload, Depends(get_current_user)],
) -> TokenPayload:
    """Dependency that requires admin role."""
    if not current_user.is_admin:
        raise ForbiddenError("Admin access required.")
    return current_user


CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]
AdminUser = Annotated[TokenPayload, Depends(require_admin)]
