"""
DATA ENGINE — Telemetry Event Queue
Redis-backed durable event queue for cross-service telemetry emission.
"""

import json
import structlog
from datetime import datetime, UTC
from typing import Any

import redis.asyncio as aioredis

from configs.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

TELEMETRY_QUEUE_KEY = "data_engine:telemetry:events"
TELEMETRY_DEAD_LETTER_KEY = "data_engine:telemetry:dead_letter"


class RedisEventQueue:
    """
    Redis list-backed event queue.
    Producers LPUSH events; consumers BRPOP from the right.
    Dead-letter queue captures events that fail processing.
    """

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info("redis_event_queue_connected")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def push(self, event_type: str, service: str, payload: dict[str, Any]) -> None:
        """Push a new event onto the queue."""
        if not self._client:
            logger.warning("redis_queue_not_connected")
            return

        event = {
            "event_type": event_type,
            "service": service,
            "payload": payload,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await self._client.lpush(TELEMETRY_QUEUE_KEY, json.dumps(event))

    async def pop(self, timeout: int = 5) -> dict | None:
        """
        Blocking right-pop from the queue.
        Returns None if no event arrives within timeout seconds.
        """
        if not self._client:
            return None

        result = await self._client.brpop(TELEMETRY_QUEUE_KEY, timeout=timeout)
        if result:
            _, data = result
            return json.loads(data)
        return None

    async def push_dead_letter(self, event: dict, error: str) -> None:
        """Move a failed event to the dead-letter queue."""
        if not self._client:
            return
        event["_error"] = error
        await self._client.lpush(TELEMETRY_DEAD_LETTER_KEY, json.dumps(event))

    async def queue_depth(self) -> int:
        """Return current number of events in the queue."""
        if not self._client:
            return 0
        return await self._client.llen(TELEMETRY_QUEUE_KEY)

    async def dead_letter_count(self) -> int:
        """Return number of events in the dead-letter queue."""
        if not self._client:
            return 0
        return await self._client.llen(TELEMETRY_DEAD_LETTER_KEY)
