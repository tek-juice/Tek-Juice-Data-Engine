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
        "workers.celery.tasks.scraper_tasks",
        "workers.celery.tasks.sync_tasks",
        "workers.celery.tasks.aeo_tasks",
        "workers.celery.tasks.seo_tasks",
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
            "kwargs": {"sources": ["google", "bing", "news", "social_media"]},
        },
        "embed-scraped-trends-hourly": {
            "task": "tasks.embed_scraped_trends",
            "schedule": settings.scraper_interval_seconds,
        },
        "run-gap-analysis-6h": {
            "task": "tasks.run_gap_analysis_batch",
            "schedule": 21600,  # 6 hours
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
    },
)
