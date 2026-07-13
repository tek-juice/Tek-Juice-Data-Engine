"""
DATA ENGINE — Event Bus
Lightweight async event system for cross-service communication.
Services publish events; subscribers react without tight coupling.
"""

import asyncio
import structlog
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any, Callable, Awaitable

logger = structlog.get_logger(__name__)

EventHandler = Callable[[dict], Awaitable[None]]


@dataclass
class Event:
    """A system event published on the event bus."""
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    source_service: str = ""
    tenant_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class EventBus:
    """
    In-process async event bus.
    For cross-process events, use the Redis-backed queue in telemetry_service.

    Usage:
        bus = EventBus()
        bus.subscribe("document.completed", my_handler)
        await bus.publish(Event("document.completed", payload={"id": "..."}))
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._wildcard_handlers: list[EventHandler] = []

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for a specific event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        logger.debug("event_handler_registered", event_type=event_type)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Register a handler that receives all events."""
        self._wildcard_handlers.append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a handler for an event type."""
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h != handler
            ]

    async def publish(self, event: Event) -> None:
        """
        Publish an event to all registered handlers.
        Handlers are called concurrently. Failures are logged but not raised.
        """
        handlers = self._handlers.get(event.event_type, []) + self._wildcard_handlers

        if not handlers:
            return

        async def safe_call(handler: EventHandler) -> None:
            try:
                await handler(event.__dict__)
            except Exception as exc:
                logger.error(
                    "event_handler_error",
                    event_type=event.event_type,
                    handler=handler.__name__,
                    error=str(exc),
                )

        await asyncio.gather(*[safe_call(h) for h in handlers], return_exceptions=True)

    def handler_count(self, event_type: str) -> int:
        return len(self._handlers.get(event_type, []))


# ── Standard Event Types ──────────────────────────────────────────────────────
class EventTypes:
    DOCUMENT_INGESTED    = "document.ingested"
    DOCUMENT_CHUNKED     = "document.chunked"
    DOCUMENT_EMBEDDED    = "document.embedded"
    DOCUMENT_COMPLETED   = "document.completed"
    DOCUMENT_FAILED      = "document.failed"
    DOCUMENT_DELETED     = "document.deleted"
    VECTOR_STORED        = "vector.stored"
    SEARCH_PERFORMED     = "search.performed"
    GAP_DETECTED         = "gap.detected"
    SCHEMA_GENERATED     = "schema.generated"
    TREND_SCRAPED        = "trend.scraped"
    SYNC_COMPLETED       = "sync.completed"
    CACHE_INVALIDATED    = "cache.invalidated"


# Module-level event bus singleton
event_bus = EventBus()
