"""
DATA ENGINE — Stress Test Runner
Phase 4: Concurrent stress testing for embedding pipeline,
vector search, and database connection pools.

Run with:
    pytest services/testing_service/stress_tests/stress_runner.py -v
"""

import asyncio
import time
import random
import pytest


async def _concurrent_requests(coro_fn, n_concurrent: int, n_total: int) -> dict:
    """
    Run coro_fn concurrently n_concurrent at a time for n_total total calls.
    Returns a stats dict: success, errors, avg_duration_ms, max_duration_ms.
    """
    semaphore = asyncio.Semaphore(n_concurrent)
    results = []

    async def _run():
        async with semaphore:
            start = time.perf_counter()
            try:
                await coro_fn()
                elapsed = (time.perf_counter() - start) * 1000
                results.append({"success": True, "duration_ms": elapsed})
            except Exception as exc:
                elapsed = (time.perf_counter() - start) * 1000
                results.append({"success": False, "error": str(exc), "duration_ms": elapsed})

    await asyncio.gather(*[_run() for _ in range(n_total)])
    successes = [r for r in results if r["success"]]
    durations = [r["duration_ms"] for r in results]
    return {
        "total":         n_total,
        "success":       len(successes),
        "errors":        n_total - len(successes),
        "error_rate":    round((n_total - len(successes)) / n_total * 100, 2),
        "avg_ms":        round(sum(durations) / len(durations), 2) if durations else 0,
        "max_ms":        round(max(durations), 2) if durations else 0,
        "p95_ms":        round(sorted(durations)[int(len(durations) * 0.95)], 2) if durations else 0,
    }


class TestVectorSearchStress:

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_concurrent_cosine_similarity(self):
        """50 concurrent cosine similarity computations must complete in <500ms avg."""
        from services.semantic_engine.cosine_similarity import cosine_similarity

        async def run():
            a = [random.uniform(-1, 1) for _ in range(1536)]
            b = [random.uniform(-1, 1) for _ in range(1536)]
            sim = cosine_similarity(a, b)
            assert -1.0 <= sim <= 1.0

        stats = await _concurrent_requests(run, n_concurrent=50, n_total=200)
        assert stats["error_rate"] == 0.0, f"Errors: {stats['errors']}"
        assert stats["avg_ms"] < 500, f"Avg response too slow: {stats['avg_ms']}ms"


class TestChunkingStress:

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_concurrent_token_chunking(self):
        """100 concurrent chunking operations on 2000-word texts."""
        from services.chunking_service.token_chunker import TokenChunker

        chunker = TokenChunker(min_tokens=256, max_tokens=512, overlap_tokens=64)
        text = "This is a sample sentence for stress testing. " * 100  # ~900 tokens

        async def run():
            loop = asyncio.get_event_loop()
            chunks = await loop.run_in_executor(None, chunker.chunk, text)
            assert len(chunks) > 0

        stats = await _concurrent_requests(run, n_concurrent=20, n_total=100)
        assert stats["error_rate"] == 0.0
        assert stats["avg_ms"] < 1000


class TestGapDetectionStress:

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_concurrent_scoring(self):
        """200 concurrent gap score computations."""
        from services.gap_detection.scoring import compute_gap_score

        async def run():
            score = compute_gap_score(
                uncovered_count=random.randint(0, 50),
                total_trends=100,
                avg_max_similarity=random.uniform(0.4, 0.9),
            )
            assert 0.0 <= score <= 1.0

        stats = await _concurrent_requests(run, n_concurrent=50, n_total=200)
        assert stats["error_rate"] == 0.0


class TestSemanticEngineStress:

    @pytest.mark.asyncio
    @pytest.mark.stress
    async def test_concurrent_ranking(self):
        """Rank 20 candidates concurrently 50 times."""
        from services.semantic_engine.ranking import SemanticRanker

        ranker = SemanticRanker()

        async def run():
            query = [random.uniform(-1, 1) for _ in range(64)]
            candidates = [
                {
                    "id": f"item-{i}",
                    "text": f"sample text {i}",
                    "embedding": [random.uniform(-1, 1) for _ in range(64)],
                }
                for i in range(20)
            ]
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None, ranker.rank, query, candidates, 10, 0.0
            )
            assert isinstance(results, list)

        stats = await _concurrent_requests(run, n_concurrent=25, n_total=50)
        assert stats["error_rate"] == 0.0
