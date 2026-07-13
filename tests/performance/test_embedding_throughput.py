"""
DATA ENGINE — Performance Tests: Embedding Throughput
Benchmarks chunking, cosine similarity, and ranking performance.
Uses pytest-benchmark for measurement (falls back to time.perf_counter).

Run with:
    pytest tests/performance/ -v -m performance
    # With benchmarks:
    pytest tests/performance/ --benchmark-only
"""

import time
import random
import pytest

pytestmark = pytest.mark.performance


def make_vector(dim: int = 1536) -> list[float]:
    return [random.uniform(-1, 1) for _ in range(dim)]


def make_vectors(n: int, dim: int = 1536) -> list[list[float]]:
    return [make_vector(dim) for _ in range(n)]


class TestCosineSimilarityPerformance:

    def test_single_comparison_under_1ms(self):
        from services.semantic_engine.cosine_similarity import cosine_similarity
        a, b = make_vector(), make_vector()
        start = time.perf_counter()
        cosine_similarity(a, b)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 1.0, f"Single cosine similarity took {elapsed_ms:.3f}ms — expected <1ms"

    def test_100_comparisons_under_50ms(self):
        from services.semantic_engine.cosine_similarity import cosine_similarity
        pairs = [(make_vector(), make_vector()) for _ in range(100)]
        start = time.perf_counter()
        for a, b in pairs:
            cosine_similarity(a, b)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 50.0, f"100 comparisons took {elapsed_ms:.1f}ms — expected <50ms"

    def test_pairwise_100x100_under_200ms(self):
        from services.semantic_engine.cosine_similarity import pairwise_cosine_similarity
        A = make_vectors(100, 128)
        B = make_vectors(100, 128)
        start = time.perf_counter()
        result = pairwise_cosine_similarity(A, B)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert result.shape == (100, 100)
        assert elapsed_ms < 200.0, f"100x100 pairwise took {elapsed_ms:.1f}ms — expected <200ms"

    def test_top_k_1000_candidates_under_100ms(self):
        from services.semantic_engine.cosine_similarity import top_k_similar
        query = make_vector(128)
        candidates = [(f"id-{i}", make_vector(128)) for i in range(1000)]
        start = time.perf_counter()
        results = top_k_similar(query, candidates, k=10)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert len(results) == 10
        assert elapsed_ms < 100.0, f"top_k with 1000 candidates took {elapsed_ms:.1f}ms"


class TestChunkingPerformance:

    def test_chunk_10k_tokens_under_500ms(self):
        from services.chunking_service.token_chunker import TokenChunker
        chunker = TokenChunker(min_tokens=256, max_tokens=512, overlap_tokens=64)
        text = "word " * 10_000  # ~10k tokens
        start = time.perf_counter()
        chunks = chunker.chunk(text)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert len(chunks) > 0
        assert elapsed_ms < 500.0, f"10k token chunk took {elapsed_ms:.1f}ms — expected <500ms"

    def test_chunk_100k_chars_under_2s(self):
        from services.chunking_service.token_chunker import TokenChunker
        chunker = TokenChunker()
        text = "data engine processes vector embeddings for semantic search. " * 1000
        start = time.perf_counter()
        chunks = chunker.chunk(text)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert len(chunks) > 0
        assert elapsed_ms < 2000.0, f"100k char chunk took {elapsed_ms:.1f}ms"


class TestRankingPerformance:

    def test_rank_500_candidates_under_200ms(self):
        from services.semantic_engine.ranking import SemanticRanker
        ranker = SemanticRanker()
        query = make_vector(128)
        candidates = [
            {"id": f"id-{i}", "text": f"text {i}", "embedding": make_vector(128)}
            for i in range(500)
        ]
        start = time.perf_counter()
        results = ranker.rank(query, candidates, top_k=10)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert len(results) <= 10
        assert elapsed_ms < 200.0, f"Ranking 500 candidates took {elapsed_ms:.1f}ms"


class TestSEOKeywordPerformance:

    def test_keyword_analysis_10k_words_under_500ms(self):
        from services.seo_engine.keyword_analysis import KeywordAnalyser
        analyser = KeywordAnalyser()
        content = " ".join([
            "vector database semantic search embeddings HNSW indexing cosine similarity"
        ] * 500)
        keywords = ["vector database", "semantic search", "HNSW indexing", "embeddings"]
        start = time.perf_counter()
        result = analyser.analyse(content, keywords)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert result.coverage_score > 0
        assert elapsed_ms < 500.0, f"Keyword analysis (10k words) took {elapsed_ms:.1f}ms"
