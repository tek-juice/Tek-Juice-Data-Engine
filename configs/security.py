"""
DATA ENGINE  Security Configuration
JWT token management, password hashing, API key generation,
and row-level security helpers.
"""

import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Any

import hashlib

import bcrypt
from jose import JWTError, jwt

from configs.settings import get_settings

settings = get_settings()

#  Password Hashing
# Uses bcrypt directly (bypasses passlib 1.7.4 / bcrypt 4.x incompatibility).

def hash_password(plain_password: str) -> str:
    """Hash a plain-text password using bcrypt."""
    return bcrypt.hashpw(
        plain_password.encode("utf-8"),
        bcrypt.gensalt(rounds=settings.bcrypt_rounds),
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against its bcrypt hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False


#  JWT Tokens 

def create_access_token(
    subject: str,
    tenant_id: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Create a signed JWT access token.

    Args:
        subject: User identifier (e.g. user UUID or email).
        tenant_id: Tenant UUID for row-level security scoping.
        extra_claims: Additional claims to embed in the token.

    Returns:
        Signed JWT string.
    """
    expire = datetime.now(UTC) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "tenant_id": tenant_id,
        "type": "access",
        "exp": expire,
        "iat": datetime.now(UTC),
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str, tenant_id: str) -> str:
    """Create a longer-lived JWT refresh token."""
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload: dict[str, Any] = {
        "sub": subject,
        "tenant_id": tenant_id,
        "type": "refresh",
        "exp": expire,
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT token.

    Raises:
        JWTError: If token is invalid or expired.
    """
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )


def is_token_valid(token: str) -> bool:
    """Return True if the token can be decoded without error."""
    try:
        decode_token(token)
        return True
    except JWTError:
        return False


#  API Key Generation 

_API_KEY_ALPHABET = string.ascii_letters + string.digits
_API_KEY_PREFIX = "de_"  # DATA ENGINE prefix
_API_KEY_LENGTH = 48


def generate_api_key() -> str:
    """
    Generate a cryptographically secure API key.

    Returns:
        API key string in format: de_<48-char random string>
    """
    random_part = "".join(
        secrets.choice(_API_KEY_ALPHABET) for _ in range(_API_KEY_LENGTH)
    )
    return f"{_API_KEY_PREFIX}{random_part}"


def generate_secret_key(length: int = 64) -> str:
    """Generate a cryptographically secure secret key (hex)."""
    return secrets.token_hex(length // 2)


# ── Row-Level Security Helpers 

def build_rls_policy_sql(table_name: str) -> str:
    """
    Build SQL to enable row-level security on a table.
    Isolates tenant data so queries without tenant_id context return no rows.

    Args:
        table_name: Name of the table to protect.

    Returns:
        SQL string to execute.
    """
    return f"""
        ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;

        DROP POLICY IF EXISTS tenant_isolation ON {table_name};

        CREATE POLICY tenant_isolation ON {table_name}
            USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
    """


def set_tenant_context_sql(tenant_id: str) -> str:
    """
    Return SQL to set the tenant context for the current transaction.
    Must be called at the start of each request that reads/writes tenant data.
    """
    return f"SET LOCAL app.current_tenant_id = '{tenant_id}';"


#  CORS Helpers 

#  API Key Hashing

def hash_api_key(api_key: str) -> str:
    """
    Hash an API key for secure storage using SHA-256.
    Mirrors shared.utils.hashing.hash_api_key — re-exported here so callers
    can import from a single configs.security namespace.
    """
    return hashlib.sha256(api_key.encode()).hexdigest()


#  CORS Helpers

def get_cors_origins() -> list[str]:
    """Return allowed CORS origins from settings."""
    return settings.gateway_allowed_origins


#  Security Headers 

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}
