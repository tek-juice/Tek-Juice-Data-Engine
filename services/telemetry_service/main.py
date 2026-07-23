"""
DATA ENGINE — Telemetry Service
Phase 2: Global Radar — event ingestion, async worker pool, job scheduling,
and metric flushing.

Swagger UI: http://localhost:8005/docs
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Query
from prometheus_client import make_asgi_app
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from configs.constants import API_PREFIX, APP_VERSION
from configs.database import get_db_session, init_db, dispose_db
from configs.settings import get_settings
from shared.exceptions.handlers import register_exception_handlers
from shared.middleware.logging import AccessLogMiddleware
from shared.middleware.request_id import RequestIDMiddleware
from services.telemetry_service.monitor import TelemetryMonitor, TelemetryEvent
from services.telemetry_service.queue.event_queue import RedisEventQueue
from services.telemetry_service.schedulers.job_scheduler import TelemetryScheduler
from services.telemetry_service.collectors.event_collector import EventCollector

settings = get_settings()
logger = structlog.get_logger(__name__)

# ── Service-level singletons (lifecycle managed in lifespan) ──────────────────
_monitor   = TelemetryMonitor()
_queue     = RedisEventQueue()
_scheduler = TelemetryScheduler()


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("telemetry_service_starting", version=APP_VERSION)
    await init_db()               # background task — returns immediately
    await _queue.connect()        # Redis connection (best-effort; logs on fail)
    await _monitor.start()        # start async worker pool
    _scheduler.start()            # start APScheduler jobs
    yield
    _scheduler.stop()
    await _monitor.stop()
    await _queue.disconnect()
    await dispose_db()
    logger.info("telemetry_service_stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — Telemetry Service",
    description="Global Radar: event ingestion, async processing, and metric flushing.",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.is_development else None,
    redoc_url=None,
)

app.mount("/metrics", make_asgi_app())

app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.gateway_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_exception_handlers(app)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/telemetry/emit", status_code=202, tags=["telemetry"])
async def emit_event(
    event_type: str,
    service: str,
    payload: dict = {},
    tenant_id: str | None = None,
    duration_ms: int | None = None,
    status: str = "success",
):
    """Emit a telemetry event into the async worker queue (non-blocking)."""
    await _monitor.emit(TelemetryEvent(
        event_type=event_type,
        service=service,
        payload=payload,
        tenant_id=tenant_id,
        duration_ms=duration_ms,
        status=status,
    ))
    return {"queued": True, "queue_depth": _monitor.queue_depth}


@app.get(f"{API_PREFIX}/telemetry/summary", tags=["telemetry"])
async def event_summary(
    hours: int = Query(default=24, ge=1, le=168),
    tenant_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
):
    """Return aggregated event counts for the last N hours."""
    collector = EventCollector(db)
    return await collector.get_event_summary(hours=hours, tenant_id=tenant_id)


@app.get(f"{API_PREFIX}/telemetry/timeseries", tags=["telemetry"])
async def throughput_timeseries(
    hours: int = Query(default=24, ge=1, le=168),
    interval_minutes: int = Query(default=30, ge=5, le=60),
    db: AsyncSession = Depends(get_db_session),
):
    """Return per-interval event throughput for charting."""
    collector = EventCollector(db)
    return await collector.get_throughput_timeseries(hours=hours, interval_minutes=interval_minutes)


@app.get(f"{API_PREFIX}/telemetry/errors", tags=["telemetry"])
async def error_log(
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
):
    """Return the most recent failure events."""
    collector = EventCollector(db)
    return await collector.get_error_log(limit=limit)


@app.get(f"{API_PREFIX}/telemetry/queue", tags=["telemetry"])
async def queue_status():
    """Return current in-memory queue depth and buffer size."""
    return {
        "queue_depth":  _monitor.queue_depth,
        "buffer_size":  _monitor.buffer_size,
        "scheduled_jobs": _scheduler.list_jobs(),
    }


@app.get("/health", tags=["health"])
async def health():
    return {"status": "healthy", "service": "telemetry_service", "version": APP_VERSION}
