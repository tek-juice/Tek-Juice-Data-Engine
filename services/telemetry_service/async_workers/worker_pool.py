"""
DATA ENGINE — Telemetry Async Worker Pool
Concurrent background processing for telemetry collection tasks.
"""

import asyncio
import structlog
from typing import Callable, Awaitable, Any

logger = structlog.get_logger(__name__)


class WorkerPool:
    """
    Bounded async worker pool for concurrent task execution.
    Prevents unbounded concurrency while maximising throughput.
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._semaphore = asyncio.Semaphore(max_workers)
        self._max_workers = max_workers
        self._active = 0

    async def submit(
        self,
        coro_fn: Callable[..., Awaitable[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Submit a coroutine for execution within the worker pool.
        Blocks if all workers are busy until one becomes available.
        """
        async with self._semaphore:
            self._active += 1
            try:
                return await coro_fn(*args, **kwargs)
            except Exception as exc:
                logger.error("worker_pool_task_failed", error=str(exc), exc_info=True)
                raise
            finally:
                self._active -= 1

    async def run_all(
        self,
        tasks: list[tuple[Callable, tuple, dict]],
    ) -> list[Any]:
        """
        Run multiple tasks concurrently within pool bounds.

        Args:
            tasks: List of (coro_fn, args, kwargs) tuples.

        Returns:
            List of results in the same order as input tasks.
        """
        coroutines = [
            self.submit(fn, *args, **kwargs) for fn, args, kwargs in tasks
        ]
        return await asyncio.gather(*coroutines, return_exceptions=True)

    @property
    def active_workers(self) -> int:
        return self._active

    @property
    def available_slots(self) -> int:
        return self._max_workers - self._active
