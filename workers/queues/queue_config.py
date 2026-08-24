"""
DATA ENGINE — Queue Configuration
Defines Celery queues, priorities, and routing for all task types.
"""

from kombu import Queue, Exchange

# ── Exchanges ─────────────────────────────────────────────────────────────────
default_exchange    = Exchange("default",    type="direct")
pipeline_exchange   = Exchange("pipeline",   type="direct")
telemetry_exchange  = Exchange("telemetry",  type="direct")
scheduled_exchange  = Exchange("scheduled",  type="direct")

# ── Queues ─────────────────────────────────────────────────────────────────────
CELERY_QUEUES = (
    # Default catch-all
    Queue("default",    default_exchange,   routing_key="default"),
    # Document processing pipeline (ordered by priority)
    Queue("ingestion",  pipeline_exchange,  routing_key="ingestion",  queue_arguments={"x-max-priority": 10}),
    Queue("embeddings", pipeline_exchange,  routing_key="embeddings", queue_arguments={"x-max-priority": 10}),
    # Telemetry / monitoring
    Queue("telemetry",  telemetry_exchange, routing_key="telemetry"),
    # Scheduled / background
    Queue("scheduled",  scheduled_exchange, routing_key="scheduled"),
)

# ── Routing ────────────────────────────────────────────────────────────────────
CELERY_ROUTES = {
    "tasks.preprocess_document":    {"queue": "ingestion",  "routing_key": "ingestion"},
    "tasks.chunk_document":         {"queue": "ingestion",  "routing_key": "ingestion"},
    "tasks.generate_embeddings":    {"queue": "embeddings", "routing_key": "embeddings"},
    "tasks.store_vectors":          {"queue": "embeddings", "routing_key": "embeddings"},
    "tasks.run_gap_analysis":       {"queue": "scheduled",  "routing_key": "scheduled"},
    "tasks.run_gap_analysis_batch": {"queue": "scheduled",  "routing_key": "scheduled"},
    "tasks.generate_schema":        {"queue": "scheduled",  "routing_key": "scheduled"},
    "tasks.scrape_trends":          {"queue": "scheduled",  "routing_key": "scheduled"},
    "tasks.embed_scraped_trends":   {"queue": "embeddings", "routing_key": "embeddings"},
    "tasks.sync_data_pool":         {"queue": "scheduled",  "routing_key": "scheduled"},
    "tasks.flush_telemetry_buffer": {"queue": "telemetry",  "routing_key": "telemetry"},
    "tasks.service_health_check":   {"queue": "telemetry",  "routing_key": "telemetry"},
}

CELERY_DEFAULT_QUEUE    = "default"
CELERY_DEFAULT_EXCHANGE = "default"
CELERY_DEFAULT_ROUTING_KEY = "default"
