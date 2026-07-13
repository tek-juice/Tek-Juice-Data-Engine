"""
DATA ENGINE — Unit Tests: Gap Detection Scoring
Tests for gap score computation, severity classification,
priority ranking, and recommendation generation.
Run with: pytest tests/unit/test_gap_scoring.py -v
"""

import pytest
from services.gap_detection.scoring import (
    compute_gap_score,
    classify_severity,
    normalise_score,
    priority_rank,
)
from services.gap_detection.recommendation import (
    RecommendationEngine,
    Recommendation,
)
from configs.constants import GapSeverity


class TestGapScoreComputation:

    def test_high_uncovered_high_score(self):
        score = compute_gap_score(uncovered_count=90, total_trends=100, avg_max_similarity=0.2)
        assert score > 0.70

    def test_low_uncovered_low_score(self):
        score = compute_gap_score(uncovered_count=5, total_trends=100, avg_max_similarity=0.90)
        assert score < 0.20

    def test_zero_total_trends_returns_zero(self):
        assert compute_gap_score(uncovered_count=0, total_trends=0, avg_max_similarity=0.5) == 0.0

    def test_score_clamped_to_unit_range(self):
        s1 = compute_gap_score(uncovered_count=0, total_trends=100, avg_max_similarity=1.0)
        s2 = compute_gap_score(uncovered_count=100, total_trends=100, avg_max_similarity=0.0)
        assert 0.0 <= s1 <= 1.0
        assert 0.0 <= s2 <= 1.0

    def test_more_uncovered_higher_score(self):
        s_low  = compute_gap_score(uncovered_count=10, total_trends=100, avg_max_similarity=0.7)
        s_high = compute_gap_score(uncovered_count=80, total_trends=100, avg_max_similarity=0.4)
        assert s_high > s_low

    def test_score_is_float(self):
        score = compute_gap_score(uncovered_count=50, total_trends=100, avg_max_similarity=0.5)
        assert isinstance(score, float)


class TestGapSeverityClassification:

    def test_low_score_is_low(self):
        assert classify_severity(0.10) == GapSeverity.LOW

    def test_medium_score_is_medium(self):
        assert classify_severity(0.45) == GapSeverity.MEDIUM

    def test_high_score_is_high(self):
        assert classify_severity(0.70) == GapSeverity.HIGH

    def test_critical_score_is_critical(self):
        assert classify_severity(0.92) == GapSeverity.CRITICAL

    def test_boundary_medium(self):
        # Exactly at the MEDIUM threshold
        assert classify_severity(GapSeverity.MEDIUM.value if hasattr(GapSeverity.MEDIUM, "value") else 0.50) in (
            GapSeverity.MEDIUM, GapSeverity.HIGH
        )

    def test_zero_is_low(self):
        assert classify_severity(0.0) == GapSeverity.LOW

    def test_one_is_critical(self):
        assert classify_severity(1.0) == GapSeverity.CRITICAL


class TestNormaliseScore:

    def test_within_range(self):
        result = normalise_score(50.0, min_val=0.0, max_val=100.0)
        assert abs(result - 0.5) < 1e-4

    def test_min_value_is_zero(self):
        assert normalise_score(0.0, min_val=0.0, max_val=100.0) == 0.0

    def test_max_value_is_one(self):
        assert normalise_score(100.0, min_val=0.0, max_val=100.0) == 1.0

    def test_same_min_max_returns_zero(self):
        assert normalise_score(50.0, min_val=50.0, max_val=50.0) == 0.0


class TestPriorityRank:

    def test_higher_gap_score_higher_rank(self):
        r_high = priority_rank(gap_score=0.9, document_age_days=10)
        r_low  = priority_rank(gap_score=0.1, document_age_days=10)
        assert r_high > r_low

    def test_older_document_lower_priority(self):
        r_new = priority_rank(gap_score=0.5, document_age_days=30)
        r_old = priority_rank(gap_score=0.5, document_age_days=365)
        assert r_new >= r_old

    def test_more_views_higher_priority(self):
        r_high = priority_rank(gap_score=0.5, document_age_days=30, view_count=1000)
        r_low  = priority_rank(gap_score=0.5, document_age_days=30, view_count=0)
        assert r_high >= r_low

    def test_rank_in_unit_range(self):
        rank = priority_rank(gap_score=0.7, document_age_days=90, view_count=500)
        assert 0.0 <= rank <= 1.0


class TestRecommendationEngine:

    def setup_method(self):
        self.engine = RecommendationEngine()

    def test_generates_recommendations(self):
        recs = self.engine.generate(
            missing_topics=["AI safety", "vector search", "HNSW indexing"],
            covered_topics=["embeddings", "LLMs"],
            gap_score=0.6,
            severity=GapSeverity.HIGH.value,
        )
        assert len(recs) > 0
        assert all(isinstance(r, Recommendation) for r in recs)

    def test_high_severity_includes_restructure(self):
        recs = self.engine.generate(
            missing_topics=["topic1"],
            covered_topics=[],
            gap_score=0.80,
            severity=GapSeverity.HIGH.value,
        )
        action_types = [r.action_type for r in recs]
        assert "restructure" in action_types

    def test_missing_topics_create_actions(self):
        topics = ["alpha", "beta", "gamma"]
        recs = self.engine.generate(
            missing_topics=topics,
            covered_topics=[],
            gap_score=0.5,
            severity=GapSeverity.MEDIUM.value,
        )
        create_actions = [r for r in recs if r.action_type == "create"]
        created_topics = [r.topic for r in create_actions]
        for t in topics:
            assert t in created_topics

    def test_covered_topics_expand_actions(self):
        recs = self.engine.generate(
            missing_topics=[],
            covered_topics=["existing topic"],
            gap_score=0.1,
            severity=GapSeverity.LOW.value,
        )
        expand_actions = [r for r in recs if r.action_type == "expand"]
        assert len(expand_actions) >= 1

    def test_metadata_recommendation_always_present(self):
        recs = self.engine.generate(
            missing_topics=[],
            covered_topics=[],
            gap_score=0.0,
            severity=GapSeverity.LOW.value,
        )
        action_types = [r.action_type for r in recs]
        assert "update_metadata" in action_types

    def test_priority_is_sequential(self):
        recs = self.engine.generate(
            missing_topics=["t1", "t2"],
            covered_topics=["t3"],
            gap_score=0.5,
            severity=GapSeverity.MEDIUM.value,
        )
        priorities = [r.priority for r in recs]
        assert priorities == sorted(priorities)

    def test_to_dict_list(self):
        recs = self.engine.generate(["t1"], ["t2"], 0.5, GapSeverity.LOW.value)
        dicts = self.engine.to_dict_list(recs)
        assert all(isinstance(d, dict) for d in dicts)
        assert all("action_type" in d and "topic" in d for d in dicts)
