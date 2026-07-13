"""
DATA ENGINE — Rate Limiter
Redis-backed sliding window rate limiting per tenant and IP.
Phase 3: Protects all gateway endpoints from abuse.
"""

import structlog
from fastapi import Request, HTTPException, status

from configs.settings import get_settings
from services.synchronization.cache import get_cache

logger = structlog.get_logger(__name__)
settings = get_settings()

TIER_LIMITS = {
    "standard":   settings.rate_limit_standard,
    "premium":    settings.rate_limit_premium,
    "enterprise": settings.rate_limit_premium * 5,
    "internal":   999_999,
}


class RateLimiter:
    """
    Sliding window rate limiter using Redis INCR + EXPIRE.
    Limits are applied per: tenant_id + endpoint path bucket.
    """

    async def check(
        self,
        request: Request,
        tenant_id: str | None = None,
        tier: str = "standard",
    ) -> None:
        """
        Check rate limit for the current request.
        Raises HTTP 429 if limit exceeded.
        Adds rate limit headers to the request state for response injection.
        """
        limit = TIER_LIMITS.get(tier, settings.rate_limit_standard)
        window = settings.rate_limit_window_seconds

        identifier = tenant_id or request.client.host if request.client else "unknown"
        cache = await get_cache()

        under_limit = await cache.rate_limit_check(
            identifier=identifier,
            limit=limit,
            window=window,
        )

        if not under_limit:
            logger.warning("rate_limit_exceeded", identifier=identifier, tier=tier)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please slow down.",
                headers={
                    "Retry-After": str(window),
                    "X-RateLimit-Limit": str(limit),
                },
            )


async def rate_limit_dependency(request: Request) -> None:
    """FastAPI dependency for standard rate limiting."""
    tenant_id = getattr(getattr(request, "state", None), "tenant_id", None)
    limiter = RateLimiter()
    await limiter.check(request, tenant_id=tenant_id)
