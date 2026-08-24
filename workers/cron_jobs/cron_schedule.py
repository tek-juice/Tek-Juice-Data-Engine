"""
DATA ENGINE — Cron Job Schedule
Defines all periodic tasks for Celery Beat.
This supplements the inline beat_schedule in celery/app.py
with additional maintenance and monitoring jobs.
"""

from celery.schedules import crontab

# Complete beat schedule — merged with app.py inline schedule at startup
BEAT_SCHEDULE = {

    # ── Pipeline maintenance ───────────────────────────────────────────────────
    "scrape-trends-hourly": {
        "task":     "tasks.scrape_trends",
        "schedule": 3600,
        "kwargs":   {"sources": ["google", "bing", "news", "social_media"]},
        "options":  {"queue": "scheduled"},
    },
    "embed-scraped-trends-2h": {
        "task":     "tasks.embed_scraped_trends",
        "schedule": 7200,
        "options":  {"queue": "embeddings"},
    },
    "run-gap-analysis-batch-6h": {
        "task":     "tasks.run_gap_analysis_batch",
        "schedule": 21600,
        "options":  {"queue": "scheduled"},
    },

    # ── Data synchronization ───────────────────────────────────────────────────
    "sync-data-pool-15m": {
        "task":     "tasks.sync_data_pool",
        "schedule": 900,
        "options":  {"queue": "scheduled"},
    },

    # ── Telemetry ──────────────────────────────────────────────────────────────
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

    # ── Database maintenance (nightly at 02:00 UTC) ────────────────────────────
    "purge-old-telemetry-nightly": {
        "task":     "tasks.sync_data_pool",
        "schedule": crontab(hour=2, minute=0),
        "options":  {"queue": "scheduled"},
    },
}
