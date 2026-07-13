"""
DATA ENGINE — Telemetry Job Scheduler
APScheduler-based cron scheduling for recurring telemetry jobs.
Phase 2: Drives trend scraping, gap analysis, and sync jobs.
"""

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from configs.settings import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


class TelemetryScheduler:
    """
    Manages all scheduled background jobs for the telemetry service.
    Jobs are registered on startup and run on the async event loop.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._jobs: dict[str, str] = {}

    def start(self) -> None:
        """Register all jobs and start the scheduler."""
        self._register_jobs()
        self._scheduler.start()
        logger.info("telemetry_scheduler_started", jobs=list(self._jobs.keys()))

    def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        self._scheduler.shutdown(wait=False)
        logger.info("telemetry_scheduler_stopped")

    def _register_jobs(self) -> None:
        """Register all recurring telemetry jobs."""

        # Trend scraping — every hour
        job = self._scheduler.add_job(
            self._run_trend_scrape,
            trigger=IntervalTrigger(seconds=settings.scraper_interval_seconds),
            id="trend_scrape",
            name="Trend Scraping Loop",
            max_instances=1,
            coalesce=True,
        )
        self._jobs["trend_scrape"] = job.id

        # Gap analysis — every 6 hours
        job = self._scheduler.add_job(
            self._run_gap_analysis,
            trigger=IntervalTrigger(hours=6),
            id="gap_analysis",
            name="Gap Analysis",
            max_instances=1,
            coalesce=True,
        )
        self._jobs["gap_analysis"] = job.id

        # Data pool sync — every 15 minutes
        job = self._scheduler.add_job(
            self._run_sync,
            trigger=IntervalTrigger(minutes=15),
            id="data_sync",
            name="Data Pool Synchronization",
            max_instances=1,
            coalesce=True,
        )
        self._jobs["data_sync"] = job.id

        # Health check — every minute
        job = self._scheduler.add_job(
            self._run_health_check,
            trigger=IntervalTrigger(minutes=1),
            id="health_check",
            name="Service Health Check",
            max_instances=1,
        )
        self._jobs["health_check"] = job.id

    async def _run_trend_scrape(self) -> None:
        """Trigger trend scraping across all configured sources."""
        logger.info("scheduled_job_running", job="trend_scrape")
        try:
            from workers.celery.app import celery_app
            celery_app.send_task("tasks.scrape_trends", kwargs={"sources": ["google", "bing", "news"]})
        except Exception as exc:
            logger.error("trend_scrape_job_failed", error=str(exc))

    async def _run_gap_analysis(self) -> None:
        """Trigger gap analysis for recently updated documents."""
        logger.info("scheduled_job_running", job="gap_analysis")
        try:
            from workers.celery.app import celery_app
            celery_app.send_task("tasks.run_gap_analysis_batch")
        except Exception as exc:
            logger.error("gap_analysis_job_failed", error=str(exc))

    async def _run_sync(self) -> None:
        """Trigger data pool synchronization."""
        logger.info("scheduled_job_running", job="data_sync")
        try:
            from workers.celery.app import celery_app
            celery_app.send_task("tasks.sync_data_pool")
        except Exception as exc:
            logger.error("sync_job_failed", error=str(exc))

    async def _run_health_check(self) -> None:
        """Check health of all dependent services."""
        logger.debug("scheduled_job_running", job="health_check")
        from configs.database import check_db_health
        db_healthy = await check_db_health()
        if not db_healthy:
            logger.error("health_check_db_failed")

    def list_jobs(self) -> list[dict]:
        """Return info about all scheduled jobs."""
        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in self._scheduler.get_jobs()
        ]
