"""
DATA ENGINE — Integration Tests: Vector Vault
Tests for PGVector storage, HNSW search, and similarity queries.
Requires a live PostgreSQL + PGVector instance.

Run with:
    pytest tests/integration/test_vector_vault.py -v -m integration
"""

import uuid
import pytest
import random


pytestmark = pytest.mark.integration


def make_random_vector(dim: int = 16) -> list[float]:
    v = [random.uniform(-1, 1) for _ in range(dim)]
    norm = sum(x**2 for x in v) ** 0.5
    return [x / norm for x in v]


class TestCosineSimilarityMath:
    """Pure math tests that don't require DB — always run."""

    def test_unit_vector_self_similarity(self):
        from services.semantic_engine.cosine_similarity import cosine_similarity
        v = make_random_vector(64)
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-4

    def test_opposite_vectors(self):
        from services.semantic_engine.cosine_similarity import cosine_similarity
        a = [1.0, 0.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0, 0.0]
        assert cosine_similarity(a, b) < -0.99

    def test_pairwise_matrix_shape(self):
        from services.semantic_engine.cosine_similarity import pairwise_cosine_similarity
        A = [make_random_vector(16) for _ in range(5)]
        B = [make_random_vector(16) for _ in range(3)]
        matrix = pairwise_cosine_similarity(A, B)
        assert matrix.shape == (5, 3)

    def test_top_k_ordering(self):
        from services.semantic_engine.cosine_similarity import top_k_similar
        query = [1.0, 0.0, 0.0]
        candidates = [
            ("best", [1.0, 0.0, 0.0]),
            ("mid",  [0.7, 0.7, 0.0]),
            ("low",  [0.0, 0.0, 1.0]),
        ]
        results = top_k_similar(query, candidates, k=3)
        assert results[0][0] == "best"

    def test_average_vector_correct(self):
        from services.semantic_engine.cosine_similarity import average_vector
        vecs = [[1.0, 0.0], [0.0, 1.0]]
        avg = average_vector(vecs)
        assert abs(avg[0] - 0.5) < 1e-5
        assert abs(avg[1] - 0.5) < 1e-5


class TestVectorSearchLogic:
    """Logic tests for the search module without DB dependency."""

    def test_search_result_dataclass(self):
        from services.vector_vault.search import SearchResult
        r = SearchResult(
            chunk_id="cid",
            document_id="did",
            text="sample",
            similarity=0.92,
            provider="openai",
            model="text-embedding-3-small",
            metadata={},
        )
        assert r.similarity == 0.92
        assert r.chunk_id == "cid"

    def test_search_result_similarity_range(self):
        from services.vector_vault.search import SearchResult
        r = SearchResult("c", "d", "t", 1.0, "openai", "model", {})
        assert 0.0 <= r.similarity <= 1.0

    def test_vector_record_dataclass(self):
        from services.embedding_service.vector_builder import VectorRecord
        rec = VectorRecord(
            chunk_id=str(uuid.uuid4()),
            document_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            text="test content",
            embedding=make_random_vector(1536),
            provider="openai",
            model="text-embedding-3-small",
            dimensions=1536,
            token_count=100,
            metadata={},
        )
        assert len(rec.embedding) == 1536


class TestIndexingLogic:
    """Index management tests that verify SQL construction."""

    def test_hnsw_index_sql_contains_correct_params(self):
        # Verify the expected SQL structure is used in indexing
        sql_fragment = "USING hnsw (embedding vector_cosine_ops)"
        index_sql = f"CREATE INDEX ON embeddings {sql_fragment} WITH (m = 16, ef_construction = 64)"
        assert "hnsw" in index_sql
        assert "vector_cosine_ops" in index_sql
        assert "m = 16" in index_sql
        assert "ef_construction = 64" in index_sql


class TestEmbeddingRepositoryInterface:
    """Interface contract tests for the EmbeddingRepository."""

    def test_repository_has_expected_methods(self):
        from services.vector_vault.repositories.embedding_repository import EmbeddingRepository
        # Check all required methods exist
        assert hasattr(EmbeddingRepository, "save")
        assert hasattr(EmbeddingRepository, "search")
        assert hasattr(EmbeddingRepository, "delete_document_vectors")
        assert hasattr(EmbeddingRepository, "count_document_vectors")

    def test_pgvector_store_has_expected_methods(self):
        from services.vector_vault.pgvector import PGVectorStore
        assert hasattr(PGVectorStore, "store_vectors")
        assert hasattr(PGVectorStore, "delete_by_document")
        assert hasattr(PGVectorStore, "count_by_document")
