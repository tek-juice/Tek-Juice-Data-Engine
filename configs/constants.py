"""
DATA ENGINE — System-Wide Constants
Immutable values referenced across all services.
"""

from enum import Enum


# ── Application 

APP_NAME = "DATA ENGINE"
APP_VERSION = "1.0.0"
API_PREFIX = "/api/v1"
HEALTH_ENDPOINT = "/health"
METRICS_ENDPOINT = "/metrics"


# ── Supported File Types 
class SourceType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MARKDOWN = "markdown"
    JSON = "json"
    CSV = "csv"
    HTML = "html"


ALLOWED_MIME_TYPES: dict[str, SourceType] = {
    "application/pdf": SourceType.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": SourceType.DOCX,
    "text/plain": SourceType.TXT,
    "text/markdown": SourceType.MARKDOWN,
    "application/json": SourceType.JSON,
    "text/csv": SourceType.CSV,
    "text/html": SourceType.HTML,
}


# ── Embedding Providers 

class EmbeddingProvider(str, Enum):
    OPENAI = "openai"
    GEMINI = "gemini"
    VOYAGE = "voyage"
    JINA = "jina"


EMBEDDING_MODELS: dict[str, dict[str, int]] = {
    EmbeddingProvider.OPENAI: {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    },
    EmbeddingProvider.GEMINI: {
        "embedding-001": 768,
        "text-embedding-004": 768,
    },
    EmbeddingProvider.VOYAGE: {
        "voyage-large-2": 1536,
        "voyage-2": 1024,
        "voyage-code-2": 1536,
    },
    EmbeddingProvider.JINA: {
        "jina-embeddings-v2-base-en": 768,
        "jina-embeddings-v2-small-en": 512,
    },
}


# ── Chunking 

class ChunkStrategy(str, Enum):
    TOKEN = "token"
    SEMANTIC = "semantic"


DEFAULT_TOKENIZER_ENCODING = "cl100k_base"
MIN_CHUNK_TOKENS = 64
MAX_CHUNK_TOKENS = 1024
DEFAULT_CHUNK_MIN = 256
DEFAULT_CHUNK_MAX = 512
DEFAULT_CHUNK_OVERLAP = 64


# ── Vector Search 

class SimilarityMetric(str, Enum):
    COSINE = "cosine"
    EUCLIDEAN = "euclidean"
    DOT_PRODUCT = "dot_product"


DEFAULT_SIMILARITY_METRIC = SimilarityMetric.COSINE
DEFAULT_TOP_K = 10
MAX_TOP_K = 100
MIN_SIMILARITY_THRESHOLD = 0.0
MAX_SIMILARITY_THRESHOLD = 1.0
DEFAULT_SIMILARITY_THRESHOLD = 0.75


# ── Document / Ingestion Status 

class DocumentStatus(str, Enum):
    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    STORING = "storing"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"


# ── Gap Detection 

class GapSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


GAP_SCORE_THRESHOLDS: dict[GapSeverity, float] = {
    GapSeverity.LOW: 0.25,
    GapSeverity.MEDIUM: 0.50,
    GapSeverity.HIGH: 0.75,
    GapSeverity.CRITICAL: 0.90,
}


# ── Schema Types 

class SchemaType(str, Enum):
    ARTICLE = "Article"
    FAQ_PAGE = "FAQPage"
    HOW_TO = "HowTo"
    PRODUCT = "Product"
    ORGANISATION = "Organisation"
    PERSON = "Person"
    WEB_PAGE = "WebPage"
    DATASET = "Dataset"
    SOFTWARE_APPLICATION = "SoftwareApplication"


SCHEMA_ORG_CONTEXT = "https://schema.org"


# ── Telemetry Sources 

class TelemetrySource(str, Enum):
    GOOGLE = "google"
    BING = "bing"
    SOCIAL_MEDIA = "social_media"
    NEWS = "news"
    RSS = "rss"


# ── SEO / GEO 

class MetadataFormat(str, Enum):
    OPEN_GRAPH = "opengraph"
    TWITTER_CARD = "twitter"
    SCHEMA_ORG = "schema_org"
    JSON_LD = "jsonld"


class EntityType(str, Enum):
    PERSON = "Person"
    ORGANISATION = "Organisation"
    PLACE = "Place"
    PRODUCT = "Product"
    EVENT = "Event"
    CONCEPT = "Concept"
    TECHNOLOGY = "Technology"


# ── HTTP / API 

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
DEFAULT_TIMEOUT_SECONDS = 30
MAX_BULK_OPERATION_SIZE = 500

REQUEST_ID_HEADER = "X-Request-ID"
TENANT_ID_HEADER = "X-Tenant-ID"
API_VERSION_HEADER = "X-API-Version"


# ── Celery Task Names 

TASK_INGEST_DOCUMENT = "tasks.ingest_document"
TASK_CHUNK_TEXT = "tasks.chunk_text"
TASK_GENERATE_EMBEDDINGS = "tasks.generate_embeddings"
TASK_STORE_VECTORS = "tasks.store_vectors"
TASK_RUN_GAP_ANALYSIS = "tasks.run_gap_analysis"
TASK_GENERATE_SCHEMA = "tasks.generate_schema"
TASK_SCRAPE_TRENDS = "tasks.scrape_trends"
TASK_SYNC_DATA_POOL = "tasks.sync_data_pool"


# ── Cache TTLs (seconds) 

CACHE_TTL_SHORT = 60          # 1 minute
CACHE_TTL_MEDIUM = 300        # 5 minutes
CACHE_TTL_LONG = 3600         # 1 hour
CACHE_TTL_DAY = 86400         # 24 hours


# ── Error Codes 

class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    UNAUTHORISED = "UNAUTHORISED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    EMBEDDING_PROVIDER_ERROR = "EMBEDDING_PROVIDER_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    PROCESSING_ERROR = "PROCESSING_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
