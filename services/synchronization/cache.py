"""
DATA ENGINE — Cache Manager
Redis-backed caching layer for frequently accessed data.
Reduces DB load for search results, document metadata, and embeddings.
"""

import json
import structlog
from typing import Any
from functools import wraps

import redis.asyncio as aioredis

from configs.settings import get_settings
from configs.constants import CACHE_TTL_SHORT, CACHE_TTL_MEDIUM, CACHE_TTL_LONG, CACHE_TTL_DAY

logger = structlog.get_logger(__name__)
settings = get_settings()


class CacheManager:
    """Async Redis cache manager with typed get/set/delete operations."""

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def get(self, key: str) -> Any | None:
        if not self._client:
            return None
        value = await self._client.get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    async def set(self, key: str, value: Any, ttl: int = CACHE_TTL_MEDIUM) -> None:
        if not self._client:
            return
        serialised = json.dumps(value) if not isinstance(value, str) else value
        await self._client.setex(key, ttl, serialised)

    async def delete(self, key: str) -> None:
        if self._client:
            await self._client.delete(key)

    async def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching a pattern. Returns count deleted."""
        if not self._client:
            return 0
        keys = await self._client.keys(pattern)
        if keys:
            return await self._client.delete(*keys)
        return 0

    async def exists(self, key: str) -> bool:
        if not self._client:
            return False
        return bool(await self._client.exists(key))

    async def increment(self, key: str, ttl: int = CACHE_TTL_LONG) -> int:
        if not self._client:
            return 0
        val = await self._client.incr(key)
        await self._client.expire(key, ttl)
        return val

    # ── Domain-Specific Cache Helpers ─────────────────────────────────────────

    async def cache_search_results(
        self, tenant_id: str, query_hash: str, results: list, ttl: int = CACHE_TTL_SHORT
    ) -> None:
        key = f"search:{tenant_id}:{query_hash}"
        await self.set(key, results, ttl=ttl)

    async def get_cached_search(self, tenant_id: str, query_hash: str) -> list | None:
        key = f"search:{tenant_id}:{query_hash}"
        return await self.get(key)

    async def cache_document_meta(self, document_id: str, meta: dict) -> None:
        key = f"doc:meta:{document_id}"
        await self.set(key, meta, ttl=CACHE_TTL_LONG)

    async def invalidate_document(self, document_id: str) -> None:
        """Invalidate all cache entries related to a document."""
        await self.delete_pattern(f"doc:*:{document_id}")
        await self.delete_pattern(f"*:{document_id}:*")

    async def rate_limit_check(self, identifier: str, limit: int, window: int) -> bool:
        """
        Check rate limit for an identifier.
        Returns True if under limit, False if exceeded.
        """
        key = f"ratelimit:{identifier}"
        count = await self.increment(key, ttl=window)
        return count <= limit

    async def health_check(self) -> bool:
        try:
            if self._client:
                await self._client.ping()
                return True
        except Exception:
            pass
        return False


# Module-level singleton
_cache_manager: CacheManager | None = None


async def get_cache() -> CacheManager:
    """Return the global cache manager instance."""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CacheManager()
        await _cache_manager.connect()
    return _cache_manager
