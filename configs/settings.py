"""
DATA ENGINE — Central Application Settings
Loaded via pydantic-settings from environment variables / .env file.
"""

from functools import lru_cache
from typing import List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application 
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = False
    app_secret_key: str = Field(..., min_length=32)
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    app_version: str = "1.0.0"
    app_name: str = "DATA ENGINE"

    # ── API Gateway 
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000
    gateway_allowed_origins: List[str] = ["http://localhost:3000"]

    @field_validator("gateway_allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v: str | list) -> list:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

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

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
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
    default_embedding_provider: Literal["openai", "gemini", "voyage", "jina"] = "openai"
    default_embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance. Use this in FastAPI dependencies."""
    return Settings()


# Module-level singleton for non-DI usage
settings = get_settings()
