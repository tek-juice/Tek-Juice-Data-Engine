"""
DATA ENGINE — Celery Application
Central Celery app instance shared across all task modules.
Configured with Redis broker and result backend.
"""

from celery import Celery
from configs.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "data_engine",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "workers.celery.tasks.ingestion_tasks",
        "workers.celery.tasks.embedding_tasks",
        "workers.celery.tasks.telemetry_tasks",
        "workers.celery.tasks.gap_tasks",
        "workers.celery.tasks.auto_close_tasks",
        "workers.celery.tasks.writing_tasks",
        "workers.celery.tasks.webhook_tasks",
        "workers.celery.tasks.scraper_tasks",
        "workers.celery.tasks.sync_tasks",
        "workers.celery.tasks.aeo_tasks",
        "workers.celery.tasks.seo_tasks",
        "workers.celery.tasks.injection_tasks",
    ],
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Task behaviour
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_soft_time_limit=600,   # 10 min soft
    task_time_limit=900,        # 15 min hard
    # Retry defaults
    task_max_retries=3,
    task_default_retry_delay=30,
    # Result retention
    result_expires=86400,       # 24 hours
    # Worker
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=500,
    # Beat schedule for recurring tasks
    beat_schedule={
        "scrape-trends-hourly": {
            "task": "tasks.scrape_trends",
            "schedule": settings.scraper_interval_seconds,
            # "indirect" needs no API keys — Google Trends autocomplete, Reddit public JSON,
            # HN, YouTube RSS, GitHub trending, etc. Always produces results even when
            # SCRAPER_API_KEY / BING_SEARCH_API_KEY are not configured.
            "kwargs": {"sources": ["google", "bing", "news", "social_media", "indirect"]},
        },
        "embed-scraped-trends-2h": {
            "task": "tasks.embed_scraped_trends",
            "schedule": 7200,  # 2 hours — runs after scrape_trends has populated data
        },
        "run-gap-analysis-6h": {
            "task": "tasks.run_gap_analysis_batch",
            "schedule": 21600,  # 6 hours
        },
        "auto-close-gaps-batch": {
            "task": "tasks.auto_close_gaps_batch",
            "schedule": settings.gap_auto_close_interval_seconds,
        },
        "write-gap-content-batch": {
            "task": "tasks.write_gap_content_batch",
            "schedule": settings.gap_auto_close_interval_seconds,
        },
        "run-aeo-batch-6h": {
            "task": "tasks.run_aeo_analysis_batch",
            "schedule": 21600,  # 6 hours
        },
        "track-keyword-rankings-daily": {
            "task": "tasks.track_keyword_rankings",
            "schedule": settings.rank_tracking_interval_seconds,
        },
        "track-domain-authority-daily": {
            "task": "tasks.track_domain_authority",
            "schedule": settings.authority_tracking_interval_seconds,
        },
        "sync-data-pool-15m": {
            "task": "tasks.sync_data_pool",
            "schedule": 900,    # 15 minutes
        },
        # ── Automated website crawl — zero human effort after URL registration ──
        "crawl-tenant-websites-daily": {
            "task": "tasks.crawl_all_tenant_websites",
            "schedule": 86400,  # 24 hours — re-crawls every tenant's site daily
        },
        # ── Content injection — push written drafts into connected products ──────
        # Runs on the same cadence as gap auto-close so published content
        # follows immediately after drafts are written and embedded.
        "inject-drafts-batch": {
            "task":     "tasks.inject_drafts_batch",
            "schedule": settings.gap_auto_close_interval_seconds,
        },
        # ── Telemetry — previously defined only in dead cron_schedule.py ─────────
        # These two tasks never fired before. Now registered here so Beat runs them.
        "flush-telemetry-30s": {
            "task":     "tasks.flush_telemetry_buffer",
            "schedule": 30,
            "options":  {"queue": "telemetry"},
        },
        "service-health-check-1m": {
            "task":     "tasks.service_health_check",
            "schedule": 60,
            "options":  {"queue": "telemetry"},
        },
    },
)
