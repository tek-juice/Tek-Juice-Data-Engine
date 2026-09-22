"""
DATA ENGINE — Telemetry Monitor
Central coordinator for the Global Radar (Phase 2).
Manages async worker pools, event queuing, and metric flushing.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any

import structlog
from prometheus_client import Counter, Histogram, Gauge

from configs.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

# ── Prometheus Metrics ────────────────────────────────────────────────────────
EVENTS_PROCESSED = Counter(
    "data_engine_telemetry_events_total",
    "Total telemetry events processed",
    ["event_type", "service", "status"],
)
EVENT_PROCESSING_TIME = Histogram(
    "data_engine_telemetry_processing_seconds",
    "Time to process a telemetry event",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)
QUEUE_DEPTH = Gauge(
    "data_engine_telemetry_queue_depth",
    "Current telemetry event queue depth",
)
ACTIVE_WORKERS = Gauge(
    "data_engine_telemetry_active_workers",
    "Number of active telemetry worker coroutines",
)


@dataclass
class TelemetryEvent:
    event_type: str
    service: str
    payload: dict[str, Any] = field(default_factory=dict)
    tenant_id: str | None = None
    duration_ms: int | None = None
    status: str = "success"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class TelemetryMonitor:
    """
    Global Radar — async worker pool for telemetry collection and processing.

    Architecture:
    - Events are pushed to an asyncio.Queue (bounded by TELEMETRY_QUEUE_MAX_SIZE)
    - N worker coroutines drain the queue concurrently
    - Flusher coroutine periodically persists buffered events to the database
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[TelemetryEvent] = asyncio.Queue(
            maxsize=settings.telemetry_queue_max_size
        )
        self._buffer: deque[TelemetryEvent] = deque(maxlen=10_000)
        self._workers: list[asyncio.Task] = []
        self._flush_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start worker pool and flush scheduler."""
        self._running = True
        for i in range(settings.telemetry_worker_count):
            task = asyncio.create_task(
                self._worker(worker_id=i), name=f"telemetry_worker_{i}"
            )
            self._workers.append(task)

        self._flush_task = asyncio.create_task(
            self._flush_loop(), name="telemetry_flusher"
        )
        ACTIVE_WORKERS.set(settings.telemetry_worker_count)
        logger.info(
            "telemetry_monitor_started",
            workers=settings.telemetry_worker_count,
            flush_interval=settings.telemetry_flush_interval_seconds,
        )

    async def stop(self) -> None:
        """Gracefully stop all workers and flush remaining events."""
        self._running = False
        # Signal workers to stop
        for _ in self._workers:
            await self._queue.put(None)  # type: ignore[arg-type]

        await asyncio.gather(*self._workers, return_exceptions=True)

        if self._flush_task:
            self._flush_task.cancel()

        await self._flush_buffer()
        ACTIVE_WORKERS.set(0)
        logger.info("telemetry_monitor_stopped")

    async def emit(self, event: TelemetryEvent) -> None:
        """
        Non-blocking event emission.
        Drops events if queue is full (prevents backpressure on callers).
        """
        try:
            self._queue.put_nowait(event)
            QUEUE_DEPTH.set(self._queue.qsize())
        except asyncio.QueueFull:
            logger.warning(
                "telemetry_queue_full_dropping_event",
                event_type=event.event_type,
            )

    async def emit_pipeline_event(
        self,
        event_type: str,
        service: str,
        document_id: str | None = None,
        tenant_id: str | None = None,
        duration_ms: int | None = None,
        status: str = "success",
        **kwargs: Any,
    ) -> None:
        """Convenience method for emitting pipeline processing events."""
        payload = {"document_id": document_id, **kwargs}
        await self.emit(
            TelemetryEvent(
                event_type=event_type,
                service=service,
                payload=payload,
                tenant_id=tenant_id,
                duration_ms=duration_ms,
                status=status,
            )
        )

    async def _worker(self, worker_id: int) -> None:
        """Drain events from queue and buffer them for flushing."""
        logger.debug("telemetry_worker_started", worker_id=worker_id)
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                if event is None:
                    break

                start = time.perf_counter()
                self._buffer.append(event)

                EVENTS_PROCESSED.labels(
                    event_type=event.event_type,
                    service=event.service,
                    status=event.status,
                ).inc()

                EVENT_PROCESSING_TIME.observe(time.perf_counter() - start)
                QUEUE_DEPTH.set(self._queue.qsize())
                self._queue.task_done()

            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.error("telemetry_worker_error", worker_id=worker_id, error=str(exc))

    async def _flush_loop(self) -> None:
        """Periodically flush buffered events to the database."""
        while self._running:
            await asyncio.sleep(settings.telemetry_flush_interval_seconds)
            await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Write buffered events to the database."""
        if not self._buffer:
            return

        events_to_flush = list(self._buffer)
        self._buffer.clear()

        try:
            from configs.database import AsyncSessionLocal
            from sqlalchemy import text
            import json

            async with AsyncSessionLocal() as session:
                for event in events_to_flush:
                    await session.execute(
                        text("""
                            INSERT INTO telemetry_events
                                (tenant_id, event_type, service, payload, duration_ms, status)
                            VALUES
                                (:tenant_id, :event_type, :service, :payload,
                                 :duration_ms, :status)
                        """),
                        {
                            "tenant_id":   event.tenant_id,
                            "event_type":  event.event_type,
                            "service":     event.service,
                            "payload":     json.dumps(event.payload),
                            "duration_ms": event.duration_ms,
                            "status":      event.status,
                        },
                    )
                await session.commit()

            logger.debug("telemetry_buffer_flushed", count=len(events_to_flush))

        except Exception as exc:
            logger.error("telemetry_flush_failed", error=str(exc))

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)
