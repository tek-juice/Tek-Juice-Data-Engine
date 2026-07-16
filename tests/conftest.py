"""
DATA ENGINE — Pytest Configuration & Shared Fixtures
Applies to all test directories.
"""

import os
import pytest

# ── Set test environment variables before any imports 
os.environ.setdefault("APP_ENV",        "testing")
os.environ.setdefault("APP_DEBUG",      "false")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-that-is-at-least-32-chars-long!")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-that-is-at-least-32-chars!")
os.environ.setdefault("POSTGRES_PASSWORD", "test-password")


# ── Markers
def pytest_configure(config):
    config.addinivalue_line("markers", "unit:        fast, isolated unit tests")
    config.addinivalue_line("markers", "integration: tests requiring a live DB/Redis")
    config.addinivalue_line("markers", "performance: latency and throughput benchmarks")
    config.addinivalue_line("markers", "e2e:         end-to-end tests against running services")
    config.addinivalue_line("markers", "stress:      concurrent load/stress tests")


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def sample_text() -> str:
    return (
        "Vector databases store high-dimensional embeddings for semantic search. "
        "HNSW indexing enables approximate nearest-neighbour queries at scale. "
        "PGVector extends PostgreSQL with vector operations and cosine similarity. "
        "According to MIT research in 2024, vector search reduced query latency by 80%. "
        "OpenAI, Anthropic, and Google DeepMind are leading AI research organisations. "
        "The system processed 3.5 million vectors in under 10 seconds."
    )


@pytest.fixture
def sample_keywords() -> list[str]:
    return ["vector database", "semantic search", "HNSW indexing", "embeddings"]


@pytest.fixture
def unit_vector_4d() -> list[float]:
    """A normalised 4-dimensional test vector."""
    return [0.5, 0.5, 0.5, 0.5]


@pytest.fixture
def identity_vector_4d() -> list[float]:
    return [1.0, 0.0, 0.0, 0.0]


@pytest.fixture
def sample_entities() -> list[dict]:
    return [
        {"text": "OpenAI",       "entity_type": "Organisation", "confidence": 0.9, "char_start": 0,  "char_end": 6},
        {"text": "GPT-4",        "entity_type": "Product",      "confidence": 0.85,"char_start": 20, "char_end": 25},
        {"text": "San Francisco","entity_type": "Place",        "confidence": 0.8, "char_start": 40, "char_end": 53},
    ]


@pytest.fixture
def valid_article_schema() -> dict:
    return {
        "@context":      "https://schema.org",
        "@type":         "Article",
        "headline":      "Vector Database Guide",
        "author":        {"@type": "Person", "name": "Jane Doe"},
        "datePublished": "2025-07-11",
        "description":   "A guide to vector databases and semantic search.",
    }
