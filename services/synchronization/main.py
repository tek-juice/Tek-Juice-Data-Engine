"""
DATA ENGINE — Synchronization Service
Phase 3: Data pool sync, cache invalidation, replication, and event broadcasting.

Swagger UI: http://localhost:8010/docs
ReDoc:      http://localhost:8010/redoc
"""

from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, HTTPException, Body
from prometheus_client import make_asgi_app
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from configs.settings import get_settings
from configs.constants import API_PREFIX, APP_VERSION
from configs.database import init_db, dispose_db
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.request_id import RequestIDMiddleware
from shared.middleware.logging import AccessLogMiddleware
from services.synchronization.sync import DataPoolSynchronizer as SyncManager
from services.synchronization.cache import CacheManager

settings = get_settings()
logger = structlog.get_logger(__name__)


# ── Models ────────────────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    sync_type: str = Field(..., description="Type of sync: full | incremental | cache")
    tenant_id: str | None = None
    resource: str | None = Field(None, description="Specific resource to sync: documents | vectors | schemas")

    model_config = {"json_schema_extra": {"example": {
        "sync_type": "incremental",
        "tenant_id": "uuid",
        "resource": "vectors",
    }}}


class CacheInvalidateRequest(BaseModel):
    keys: list[str] = Field(..., description="Cache keys to invalidate")
    tenant_id: str | None = None


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("synchronization_service_starting", version=APP_VERSION)
    await init_db()
    yield
    await dispose_db()
    logger.info("synchronization_service_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — Synchronization Service",
    description="""
## Synchronization Service

Manages data consistency across services — cache invalidation, data pool sync,
replication, and event broadcasting.

### Sync Types
- **full** — Complete resync of all data for a tenant
- **incremental** — Sync only changed records since last sync
- **cache** — Invalidate Redis cache entries without DB sync
    """,
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "sync", "description": "Synchronization operations"},
        {"name": "cache", "description": "Cache management"},
        {"name": "health", "description": "Service health"},
    ],
)

app.mount("/metrics", make_asgi_app())

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.gateway_allowed_origins,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
register_exception_handlers(app)

_sync = SyncManager()
_cache = CacheManager()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/sync/trigger", tags=["sync"],
          summary="Trigger a data synchronization")
async def trigger_sync(request: SyncRequest = Body(...)):
    """Trigger a synchronization run for the specified type and resource."""
    try:
        result = await _sync.run(
            sync_type=request.sync_type,
            tenant_id=request.tenant_id,
            resource=request.resource,
        )
        return {"status": "completed", "synced": result}
    except Exception as exc:
        logger.error("sync_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(f"{API_PREFIX}/cache/invalidate", tags=["cache"],
          summary="Invalidate cache keys")
async def invalidate_cache(request: CacheInvalidateRequest = Body(...)):
    """Invalidate specific Redis cache keys."""
    try:
        deleted = await _cache.invalidate(keys=request.keys, tenant_id=request.tenant_id)
        return {"invalidated": deleted, "keys": request.keys}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get(f"{API_PREFIX}/sync/status", tags=["sync"],
         summary="Get last sync status")
async def sync_status():
    """Returns the status and timestamp of the last sync run."""
    try:
        from configs.database import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT * FROM sync_log ORDER BY started_at DESC LIMIT 5")
            )
            rows = [dict(r._mapping) for r in result.fetchall()]
        return {"recent_syncs": rows}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health", tags=["health"], summary="Health check")
async def health():
    return {"status": "healthy", "service": "synchronization", "version": APP_VERSION}
