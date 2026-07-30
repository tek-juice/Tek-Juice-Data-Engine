"""
DATA ENGINE — Central Application Settings
Loaded via pydantic-settings from environment variables / .env file.
"""

from pathlib import Path
from typing import List, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve paths relative to this file — works regardless of working directory
_ROOT = Path(__file__).resolve().parent.parent   # project root
_ENV = _ROOT / ".env"
_ENV_LOCAL = _ROOT / ".env.local"

# .env.local overrides .env when running outside Docker (python run.py / local dev).
# Inside Docker containers .env.local won't exist, so only .env is loaded.
_ENV_FILES = [str(_ENV)] + ([str(_ENV_LOCAL)] if _ENV_LOCAL.exists() else [])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,          # later files override earlier ones
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application
    app_env: Literal["development", "staging", "production", "testing"] = "development"
    app_debug: bool = False
    app_secret_key: str = Field(..., min_length=32)
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    app_version: str = "1.0.0"
    app_name: str = "DATA ENGINE"

    # ── API Gateway
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000
    # Pydantic-settings v2 tries json.loads() on List fields from .env before
    # calling validators.  Storing as str and exposing gateway_allowed_origins
    # as a @property prevents the JSON-parse error on comma-separated values.
    _gateway_allowed_origins_raw: str = ""
    gateway_allowed_origins_str: str = Field(
        default="", alias="gateway_allowed_origins"
    )

    @property
    def gateway_allowed_origins(self) -> List[str]:
        """Return parsed list of CORS origins from the comma-separated env var."""
        raw = self.gateway_allowed_origins_str
        if not raw:
            return []
        return [o.strip() for o in raw.split(",") if o.strip()]

    # ── PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "data_engine"
    postgres_user: str = "data_engine_user"
    postgres_password: str = Field(..., min_length=8)
    postgres_pool_size: int = 20
    postgres_max_overflow: int = 10
    postgres_pool_timeout: int = 30
    postgres_echo: bool = False

    # ── PgBouncer — Connection Pooler
    # All services connect through PgBouncer, NOT directly to PostgreSQL.
    # PgBouncer runs on port 6432 and multiplexes connections to PostgreSQL.
    # Set pgbouncer_enabled=False only for migrations (alembic needs direct connection).
    pgbouncer_host: str = "localhost"
    pgbouncer_port: int = 6432
    pgbouncer_enabled: bool = True

    @property
    def database_url(self) -> str:
        """
        Returns the async database URL.
        Routes through PgBouncer when enabled (production/staging).
        Falls back to direct PostgreSQL for migrations and development.
        Password is URL-encoded to handle special characters like @ # % etc.

        Query string params:
          ssl=disable                       — PgBouncer uses plain auth inside Docker
          prepared_statement_cache_size=0   — PgBouncer transaction mode does not
                                              support prepared statements; disable
                                              asyncpg's cache entirely.
        """
        from urllib.parse import quote_plus
        host = self.pgbouncer_host if self.pgbouncer_enabled else self.postgres_host
        port = self.pgbouncer_port if self.pgbouncer_enabled else self.postgres_port
        pw   = quote_plus(self.postgres_password)
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{pw}"
            f"@{host}:{port}/{self.postgres_db}"
            f"?ssl=disable&prepared_statement_cache_size=0"
        )

    @property
    def sync_database_url(self) -> str:
        """Sync URL for Alembic migrations — always connects directly to PostgreSQL."""
        from urllib.parse import quote_plus
        pw = quote_plus(self.postgres_password)
        return (
            f"postgresql://{self.postgres_user}:{pw}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Redis 
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ── Celery 
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── Embedding Providers 
    openai_api_key: str = ""
    gemini_api_key: str = ""
    voyage_api_key: str = ""
    jina_api_key: str = ""
    default_embedding_provider: Literal["openai", "gemini", "voyage", "jina"] = "gemini"
    default_embedding_model: str = "text-embedding-004"
    embedding_dimension: int = 768
    embedding_batch_size: int = 100
    embedding_max_retries: int = 5

    # ── Chunking 
    chunk_min_tokens: int = 256
    chunk_max_tokens: int = 512
    chunk_overlap_tokens: int = 64
    semantic_chunk_similarity_threshold: float = 0.85

    # ── Vector Vault 
    vector_similarity_threshold: float = 0.75
    vector_top_k: int = 10
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64

    # ── Google OAuth 2.0
    # Credentials from https://console.cloud.google.com → APIs & Services → Credentials.
    # Authorised redirect URI to register: <BACKEND_URL>/auth/oauth/google/callback
    google_client_id: str = ""
    google_client_secret: str = ""

    # ── Security
    jwt_secret_key: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30
    api_key_header: str = "X-API-Key"
    bcrypt_rounds: int = 12

    # ── Trend Scraper
    google_search_api_key: str = ""
    google_search_cx: str = ""
    bing_search_api_key: str = ""
    scraper_interval_seconds: int = 3600

    # ── Gap Auto-Closure
    # How often (seconds) the auto-close batch sweep runs across all documents.
    # Defaults to 6 hours. Documents with open gap_close_actions are prioritised.
    gap_auto_close_interval_seconds: int = 21600   # 6 hours

    # ── Webhooks
    # Timeout for outbound webhook POST requests (seconds).
    webhook_timeout_seconds: float = 15.0
    # Maximum automatic retry attempts per endpoint before giving up.
    webhook_max_retries: int = 3

    # ── LLM Writing Agent
    # Provider used to generate gap-filling content drafts.
    # Options: "gemini" | "openai"
    # Falls back to openai automatically if the gemini key is absent.
    llm_writing_provider: str = "gemini"
    # Model name for the writing provider.
    # Gemini:  gemini-2.5-flash | gemini-2.5-pro | gemini-2.0-flash
    # OpenAI:  gpt-4o | gpt-4o-mini | gpt-4-turbo
    llm_writing_model: str = "gemini-2.5-flash"
    # Maximum tokens the writing agent may generate per content section.
    llm_writing_max_tokens: int = 1200

    # ── DataForSEO — SERP Rank Tracking & Backlink Authority
    # Credentials: https://app.dataforseo.com/api-dashboard
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    # Rank tracking: how often to refresh keyword rankings (seconds)
    rank_tracking_interval_seconds: int = 86400   # 24 hours
    # Authority: how often to refresh domain authority snapshots (seconds)
    authority_tracking_interval_seconds: int = 86400  # 24 hours

    # ── NER / spaCy
    # Set to False to force rule-based entity extraction (no spaCy required)
    spacy_ner_enabled: bool = True
    # Set to True to enable Wikidata sameAs linking for entities
    wikidata_linking_enabled: bool = True

    # ── Proxy & Anti-blocking
    proxy_url: str = ""
    proxy_pool: str = ""
    scraper_api_key: str = ""
    scraper_rate_limit_rpm: int = 30
    scraper_request_delay: float = 2.0

    # ── Indirect / Public Signal Channels (no API keys required)
    # Enable/disable individual public fallback channels independently.
    # All default to True — set False to disable a specific channel.
    indirect_signals_enabled: bool = True
    indirect_nitter_enabled: bool = True        # Twitter via nitter.net RSS
    indirect_reddit_enabled: bool = True        # Reddit public JSON (no OAuth)
    indirect_tiktok_web_enabled: bool = True    # TikTok web session scraping
    indirect_youtube_rss_enabled: bool = True   # YouTube trending RSS (no API key)
    indirect_github_enabled: bool = True        # GitHub trending repos
    indirect_wikipedia_enabled: bool = True     # Wikipedia pageviews API
    indirect_medium_enabled: bool = True        # Medium tag RSS
    indirect_google_trends_enabled: bool = True # Google autocomplete + daily trends
    indirect_instagram_public_enabled: bool = False  # Instagram public GQL (aggressive rate-limit, off by default)

    # Nitter instance pool override (comma-separated; leave blank to use built-in list)
    nitter_instances: str = ""

    # ── Warm Session Config
    # How many warm browser sessions to maintain per platform domain
    session_pool_size: int = 3
    # How long a warm session stays valid before being recycled (seconds)
    session_ttl_seconds: int = 1800

    @property
    def proxy_list(self) -> list[str]:
        """Parse comma-separated proxy pool into a list."""
        if not self.proxy_pool:
            return [self.proxy_url] if self.proxy_url else []
        return [p.strip() for p in self.proxy_pool.split(",") if p.strip()]

    # ── Headless Browser (Playwright)
    headless_browser_enabled: bool = True
    playwright_browser: str = "chromium"
    headless_max_concurrent: int = 3

    # ── Social Media Scrapers
    # Reddit (https://www.reddit.com/prefs/apps)
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "DataEngine/1.0"

    # X / Twitter (https://developer.twitter.com)
    twitter_bearer_token: str = ""

    # Facebook / Instagram (https://developers.facebook.com)
    facebook_access_token: str = ""
    instagram_access_token: str = ""

    # TikTok (https://developers.tiktok.com)
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""

    # Snapchat (https://developers.snap.com)
    snapchat_access_token: str = ""

    # YouTube (https://console.cloud.google.com — same project as Google Search)
    youtube_api_key: str = ""

    # ── Telemetry 
    telemetry_worker_count: int = 4
    telemetry_queue_max_size: int = 10000
    telemetry_flush_interval_seconds: int = 30

    # ── Storage 
    storage_base_path: str = "./storage"
    max_upload_size_mb: int = 100

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    # ── Monitoring 
    prometheus_port: int = 9090
    grafana_port: int = 3000
    grafana_admin_password: str = "admin"

    # ── Service Ports 
    ingestion_service_port: int = 8001
    chunking_service_port: int = 8002
    embedding_service_port: int = 8003
    vector_vault_port: int = 8004
    telemetry_service_port: int = 8005
    trend_scraper_port: int = 8006
    semantic_engine_port: int = 8007
    gap_detection_port: int = 8008
    schema_factory_port: int = 8009
    sync_service_port: int = 8010
    dashboard_backend_port: int = 8011
    seo_engine_port: int = 8012
    geo_engine_port: int = 8013
    aeo_engine_port: int = 8014

    # ── Rate Limiting 
    rate_limit_standard: int = 100       # requests per minute
    rate_limit_premium: int = 1000
    rate_limit_window_seconds: int = 60

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


def get_settings() -> Settings:
    """Return a fresh Settings instance reading current environment variables."""
    return Settings()


# Module-level singleton used across the codebase.
# Never caches — always reads from os.environ so Docker overrides always apply.
class _LazySettings:
    """Always-fresh settings proxy — no caching, no stale env values."""

    def __getattr__(self, name: str):
        return getattr(Settings(), name)


settings: Settings = _LazySettings()  # type: ignore[assignment]
