"""
DATA ENGINE — API Key Manager
Manages API key lifecycle: generation, validation, rotation, and revocation.
"""

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from configs.security import generate_api_key, hash_api_key

logger = structlog.get_logger(__name__)


class APIKeyManager:
    """Handles all API key database operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_keys(self, tenant_id: str) -> list[dict]:
        """List all active API keys for a tenant (prefix only — never full key)."""
        result = await self._session.execute(
            text("""
                SELECT id, key_prefix, name, is_active, last_used_at, created_at, expires_at
                FROM api_keys
                WHERE tenant_id = :tenant_id
                ORDER BY created_at DESC
            """),
            {"tenant_id": tenant_id},
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def validate_key(self, raw_key: str) -> dict | None:
        """Validate an API key and return its tenant context."""
        key_hash = hash_api_key(raw_key)
        result = await self._session.execute(
            text("""
                SELECT ak.id, ak.tenant_id, ak.is_active, t.is_active AS tenant_active
                FROM api_keys ak
                JOIN tenants t ON t.id = ak.tenant_id
                WHERE ak.key_hash = :key_hash
                  AND (ak.expires_at IS NULL OR ak.expires_at > NOW())
            """),
            {"key_hash": key_hash},
        )
        row = result.fetchone()
        if not row or not row.is_active or not row.tenant_active:
            return None

        await self._session.execute(
            text("UPDATE api_keys SET last_used_at = NOW() WHERE id = :id"),
            {"id": str(row.id)},
        )
        return {"key_id": str(row.id), "tenant_id": str(row.tenant_id)}

    async def rotate_key(self, key_id: str, tenant_id: str) -> str:
        """Revoke an existing key and generate a new one with the same name."""
        result = await self._session.execute(
            text("SELECT name FROM api_keys WHERE id = :id AND tenant_id = :tenant_id"),
            {"id": key_id, "tenant_id": tenant_id},
        )
        row = result.fetchone()
        if not row:
            raise ValueError("API key not found.")

        await self._session.execute(
            text("UPDATE api_keys SET is_active = false WHERE id = :id"),
            {"id": key_id},
        )

        new_key = generate_api_key()
        new_hash = hash_api_key(new_key)
        import uuid
        await self._session.execute(
            text("""
                INSERT INTO api_keys (id, tenant_id, key_hash, key_prefix, name)
                VALUES (:id, :tenant_id, :key_hash, :prefix, :name)
            """),
            {
                "id":        str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "key_hash":  new_hash,
                "prefix":    new_key[:12],
                "name":      row.name,
            },
        )
        logger.info("api_key_rotated", old_key_id=key_id, tenant_id=tenant_id)
        return new_key
