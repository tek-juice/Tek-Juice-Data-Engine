"""
DATA ENGINE — Load Tests
Phase 4: Locust-based load testing for production validation.
Simulates realistic user traffic patterns across all major API endpoints.

Run with:
    locust -f services/testing_service/load_tests/locustfile.py \
        --host http://localhost:8000 \
        --users 100 \
        --spawn-rate 10 \
        --run-time 5m \
        --headless
"""

import json
import random
from locust import HttpUser, task, between, events


# ── Shared test data ───────────────────────────────────────────────────────────
SAMPLE_TEXTS = [
    "Vector databases enable fast semantic search using high-dimensional embeddings.",
    "Large language models process text by converting tokens to vector representations.",
    "HNSW indexing supports approximate nearest-neighbour search with high recall.",
    "Generative AI models require dense, entity-rich content for reliable citation.",
    "Production data pipelines must handle ingestion, chunking, and embedding at scale.",
]

SAMPLE_QUERIES = [
    "what is vector search",
    "how does semantic embedding work",
    "explain HNSW indexing",
    "generative AI content optimisation",
    "data pipeline architecture",
]


def get_auth_headers(client, username: str = "admin@tekjuice.ai", password: str = "ChangeMe123!") -> dict:
    """Obtain JWT token and return auth headers."""
    response = client.post(
        "/api/v1/auth/token",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code == 200:
        token = response.json().get("access_token", "")
        return {"Authorization": f"Bearer {token}"}
    return {}


# ── Standard API User ──────────────────────────────────────────────────────────
class APIUser(HttpUser):
    """
    Simulates a standard tenant user.
    Wait time: 1–5 seconds between requests (realistic human pacing).
    """
    wait_time = between(1, 5)
    auth_headers: dict = {}

    def on_start(self):
        """Authenticate on session start."""
        self.auth_headers = get_auth_headers(self.client)

    @task(3)
    def health_check(self):
        """Health checks are the most frequent operation."""
        with self.client.get("/health", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"Health check failed: {response.status_code}")

    @task(2)
    def list_documents(self):
        """Browse document list."""
        self.client.get(
            "/api/v1/ingest/documents?page=1&page_size=20",
            headers=self.auth_headers,
            name="/api/v1/ingest/documents",
        )

    @task(2)
    def vector_search(self):
        """Perform semantic search — core operation."""
        query = random.choice(SAMPLE_QUERIES)
        self.client.post(
            "/api/v1/vectors/search",
            json={
                "query_embedding": [round(random.uniform(-1, 1), 6) for _ in range(1536)],
                "top_k": 10,
                "similarity_threshold": 0.70,
            },
            headers=self.auth_headers,
            name="/api/v1/vectors/search",
        )

    @task(1)
    def dashboard_overview(self):
        """Load dashboard overview."""
        self.client.get(
            "/api/v1/dashboard/overview",
            headers=self.auth_headers,
            name="/api/v1/dashboard/overview",
        )

    @task(1)
    def metrics_summary(self):
        """Load metrics summary."""
        self.client.get(
            "/api/v1/dashboard/metrics/summary?hours=24",
            headers=self.auth_headers,
            name="/api/v1/dashboard/metrics/summary",
        )


# ── High-Volume Embedding User ─────────────────────────────────────────────────
class EmbeddingUser(HttpUser):
    """
    Simulates embedding generation workloads.
    Higher wait time — embedding is compute-heavy.
    """
    wait_time = between(2, 8)
    auth_headers: dict = {}

    def on_start(self):
        self.auth_headers = get_auth_headers(self.client)

    @task
    def generate_embedding(self):
        """Embed a random text sample."""
        text = random.choice(SAMPLE_TEXTS)
        self.client.post(
            "/api/v1/embed",
            json={"texts": [text], "provider": "openai"},
            headers=self.auth_headers,
            name="/api/v1/embed",
        )


# ── Ingestion User ─────────────────────────────────────────────────────────────
class IngestionUser(HttpUser):
    """Simulates document upload and status polling."""
    wait_time = between(5, 15)
    auth_headers: dict = {}

    def on_start(self):
        self.auth_headers = get_auth_headers(self.client)

    @task
    def check_document_status(self):
        """Poll a known document ID for status."""
        # In real tests, use a pre-seeded document ID
        fake_doc_id = "00000000-0000-0000-0000-000000000001"
        self.client.get(
            f"/api/v1/ingest/status/{fake_doc_id}",
            headers=self.auth_headers,
            name="/api/v1/ingest/status/{document_id}",
        )


# ── Event Hooks ───────────────────────────────────────────────────────────────
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n=== DATA ENGINE Load Test Started ===")
    print(f"Target: {environment.host}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("\n=== DATA ENGINE Load Test Complete ===")
    stats = environment.stats.total
    print(f"Total Requests: {stats.num_requests}")
    print(f"Failures: {stats.num_failures}")
    print(f"Avg Response Time: {stats.avg_response_time:.1f}ms")
    print(f"RPS: {stats.current_rps:.1f}")
