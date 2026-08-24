"""
DATA ENGINE — Unit Tests: Semantic Engine
Tests for cosine similarity, clustering, ranking, and comparator.
Run with: pytest tests/unit/test_semantic_engine.py -v
"""

import math
import pytest
from services.semantic_engine.cosine_similarity import (
    cosine_similarity,
    cosine_distance,
    euclidean_distance,
    dot_product_similarity,
    pairwise_cosine_similarity,
    average_vector,
    normalise_vector,
    top_k_similar,
)
from services.semantic_engine.ranking import SemanticRanker, RankedResult
from services.semantic_engine.comparator import SemanticComparator


class TestCosineSimilarity:

    def test_identical_vectors(self):
        v = [0.1, 0.2, 0.3, 0.4]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-5

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(cosine_similarity(a, b)) < 1e-5

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) < -0.99

    def test_range_minus_one_to_one(self):
        import random
        for _ in range(20):
            a = [random.uniform(-1, 1) for _ in range(128)]
            b = [random.uniform(-1, 1) for _ in range(128)]
            sim = cosine_similarity(a, b)
            assert -1.0 <= sim <= 1.0

    def test_dimension_mismatch_raises(self):
        with pytest.raises(ValueError):
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])

    def test_zero_vector_returns_zero(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_cosine_distance_complement(self):
        a = [0.5, 0.5]
        b = [0.3, 0.7]
        sim = cosine_similarity(a, b)
        dist = cosine_distance(a, b)
        assert abs(sim + dist - 1.0) < 1e-5

    def test_euclidean_distance_same_vector(self):
        v = [1.0, 2.0, 3.0]
        assert euclidean_distance(v, v) < 1e-5

    def test_euclidean_distance_positive(self):
        a = [0.0, 0.0]
        b = [3.0, 4.0]
        assert abs(euclidean_distance(a, b) - 5.0) < 1e-5

    def test_pairwise_shape(self):
        import numpy as np
        matrix_a = [[1.0, 0.0], [0.0, 1.0]]
        matrix_b = [[1.0, 0.0], [0.5, 0.5], [-1.0, 0.0]]
        result = pairwise_cosine_similarity(matrix_a, matrix_b)
        assert result.shape == (2, 3)

    def test_average_vector(self):
        vecs = [[1.0, 0.0], [0.0, 1.0]]
        avg = average_vector(vecs)
        assert abs(avg[0] - 0.5) < 1e-5
        assert abs(avg[1] - 0.5) < 1e-5

    def test_average_vector_empty_raises(self):
        with pytest.raises(ValueError):
            average_vector([])

    def test_normalise_vector_unit_length(self):
        v = [3.0, 4.0]
        n = normalise_vector(v)
        length = math.sqrt(sum(x**2 for x in n))
        assert abs(length - 1.0) < 1e-5

    def test_normalise_zero_vector_unchanged(self):
        v = [0.0, 0.0]
        assert normalise_vector(v) == v

    def test_top_k_similar_ordering(self):
        query = [1.0, 0.0]
        candidates = [
            ("c1", [1.0, 0.0]),   # identical
            ("c2", [0.0, 1.0]),   # orthogonal
            ("c3", [0.7, 0.7]),   # similar
        ]
        results = top_k_similar(query, candidates, k=3)
        assert results[0][0] == "c1"
        assert results[0][1] > results[1][1]

    def test_top_k_threshold_filters(self):
        query = [1.0, 0.0]
        candidates = [("c1", [1.0, 0.0]), ("c2", [0.0, 1.0])]
        results = top_k_similar(query, candidates, k=10, threshold=0.5)
        ids = [r[0] for r in results]
        assert "c1" in ids
        assert "c2" not in ids


class TestSemanticRanker:

    def setup_method(self):
        self.ranker = SemanticRanker(boost_weight=0.0)

    def test_ranks_by_similarity(self):
        query = [1.0, 0.0, 0.0]
        candidates = [
            {"id": "a", "text": "best match",   "embedding": [1.0, 0.0, 0.0]},
            {"id": "b", "text": "partial match","embedding": [0.7, 0.3, 0.0]},
            {"id": "c", "text": "no match",     "embedding": [0.0, 1.0, 0.0]},
        ]
        results = self.ranker.rank(query, candidates, top_k=3)
        assert results[0].id == "a"
        assert results[0].final_score > results[1].final_score

    def test_top_k_limit_respected(self):
        query = [1.0, 0.0]
        candidates = [{"id": str(i), "text": "t", "embedding": [float(i)/10, 0.0]} for i in range(10)]
        results = self.ranker.rank(query, candidates, top_k=3)
        assert len(results) <= 3

    def test_threshold_filters_low_similarity(self):
        query = [1.0, 0.0]
        candidates = [
            {"id": "a", "text": "high", "embedding": [0.99, 0.0]},
            {"id": "b", "text": "low",  "embedding": [0.0, 1.0]},
        ]
        results = self.ranker.rank(query, candidates, top_k=10, threshold=0.9)
        ids = [r.id for r in results]
        assert "a" in ids
        assert "b" not in ids

    def test_returns_ranked_result_objects(self):
        query = [1.0, 0.0]
        candidates = [{"id": "x", "text": "txt", "embedding": [0.8, 0.2]}]
        results = self.ranker.rank(query, candidates)
        assert all(isinstance(r, RankedResult) for r in results)

    def test_empty_candidates_returns_empty(self):
        assert self.ranker.rank([1.0, 0.0], []) == []


class TestSemanticComparator:

    def setup_method(self):
        self.comparator = SemanticComparator()

    def test_compare_identical_vectors(self):
        v = [0.5, 0.5, 0.0]
        result = self.comparator.compare_vectors("a", v, "b", v)
        assert abs(result.similarity - 1.0) < 1e-4
        assert result.interpretation in ("near-duplicate", "highly similar")

    def test_compare_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        result = self.comparator.compare_vectors("a", a, "b", b)
        assert result.interpretation == "semantically distinct"

    def test_compare_document_sets(self):
        vecs_a = [[1.0, 0.0], [0.9, 0.1]]
        vecs_b = [[1.0, 0.0], [0.8, 0.2]]
        result = self.comparator.compare_document_sets("doc-a", vecs_a, "doc-b", vecs_b)
        assert result.similarity > 0.9

    def test_empty_document_set_returns_zero(self):
        result = self.comparator.compare_document_sets("a", [], "b", [[1.0, 0.0]])
        assert result.similarity == 0.0

    def test_coverage_score_perfect(self):
        doc_vecs   = [[1.0, 0.0], [0.0, 1.0]]
        trend_vecs = [[1.0, 0.0], [0.0, 1.0]]
        score = self.comparator.average_coverage_score(doc_vecs, trend_vecs, threshold=0.9)
        assert score == 1.0

    def test_coverage_score_zero(self):
        doc_vecs   = [[1.0, 0.0]]
        trend_vecs = [[0.0, 1.0]]
        score = self.comparator.average_coverage_score(doc_vecs, trend_vecs, threshold=0.99)
        assert score == 0.0

    def test_coverage_matrix_shape(self):
        doc_vecs   = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]]
        trend_vecs = [[1.0, 0.0], [0.0, 1.0]]
        matrix = self.comparator.coverage_matrix(doc_vecs, trend_vecs)
        assert len(matrix) == 3
        assert len(matrix[0]) == 2
