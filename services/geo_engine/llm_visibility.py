"""
DATA ENGINE — GEO LLM Visibility Scorer
Phase 4: Measures how discoverable and citable content is
across different generative AI engines (GPT-4, Gemini, Claude, Perplexity).
"""

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class LLMVisibilityScore:
    """Comprehensive LLM visibility assessment for a document."""
    overall_score: float              # 0–100
    entity_coverage_score: float      # 0–25
    citation_readiness_score: float   # 0–25
    semantic_clarity_score: float     # 0–25
    structure_score: float            # 0–25
    target_models: list[str]
    model_specific_scores: dict[str, float]
    strengths: list[str]
    improvements: list[str]
    optimised_excerpt: str | None = None


SUPPORTED_MODELS = ["gpt-4", "gemini", "claude", "perplexity", "bing-chat"]


class LLMVisibilityScorer:
    """
    Scores content for visibility in generative AI search responses.

    Scoring dimensions (25 pts each = 100 total):
    1. Entity Coverage    — named entities with Schema.org types
    2. Citation Readiness — statistics, sources, dates, quotes
    3. Semantic Clarity   — unambiguous, direct factual statements
    4. Content Structure  — headings, lists, logical flow
    """

    def score(
        self,
        content: str,
        entity_count: int = 0,
        citation_score: float = 0.0,
        context_richness: float = 0.0,
        target_models: list[str] | None = None,
    ) -> LLMVisibilityScore:
        """
        Compute LLM visibility score.

        Args:
            content:          Plain text content.
            entity_count:     Number of named entities (from EntityMapper).
            citation_score:   Citation readiness score 0–100 (from CitationReadinessAnalyser).
            context_richness: Context richness 0–100 (from SemanticOptimiser).
            target_models:    AI models to optimise for.

        Returns:
            LLMVisibilityScore with dimension breakdown.
        """
        models = target_models or SUPPORTED_MODELS

        word_count = len(content.split())

        # Dimension 1: Entity Coverage (0–25)
        entity_density = entity_count / max(word_count, 1) * 100
        entity_score = min(25.0, entity_density * 2.5)

        # Dimension 2: Citation Readiness (0–25)
        cit_score = min(25.0, citation_score * 0.25)

        # Dimension 3: Semantic Clarity (0–25)
        sem_score = min(25.0, context_richness * 0.25)

        # Dimension 4: Structure (0–25)
        struct_score = self._score_structure(content)

        overall = entity_score + cit_score + sem_score + struct_score

        # Per-model adjustments
        model_scores = self._compute_model_scores(
            overall, entity_score, cit_score, struct_score
        )

        strengths = self._identify_strengths(
            entity_score, cit_score, sem_score, struct_score
        )
        improvements = self._identify_improvements(
            entity_score, cit_score, sem_score, struct_score, word_count
        )

        return LLMVisibilityScore(
            overall_score=round(overall, 1),
            entity_coverage_score=round(entity_score, 1),
            citation_readiness_score=round(cit_score, 1),
            semantic_clarity_score=round(sem_score, 1),
            structure_score=round(struct_score, 1),
            target_models=models,
            model_specific_scores=model_scores,
            strengths=strengths,
            improvements=improvements,
        )

    def _score_structure(self, content: str) -> float:
        """Score content structure based on headings, lists, and paragraph length."""
        import re
        score = 0.0
        if re.search(r'^#{1,3}\s+', content, re.MULTILINE):
            score += 8.0
        if re.search(r'^\s*[-*•]\s+', content, re.MULTILINE):
            score += 7.0
        if re.search(r'^\s*\d+[.)]\s+', content, re.MULTILINE):
            score += 5.0
        paragraphs = [p for p in content.split('\n\n') if p.strip()]
        if paragraphs:
            avg_len = sum(len(p.split()) for p in paragraphs) / len(paragraphs)
            if 50 <= avg_len <= 150:
                score += 5.0
        return min(25.0, score)

    def _compute_model_scores(
        self,
        overall: float,
        entity_score: float,
        cit_score: float,
        struct_score: float,
    ) -> dict[str, float]:
        """Compute model-specific visibility estimates."""
        return {
            "gpt-4":      round(overall * 0.98, 1),
            "gemini":     round(overall * 0.95 + entity_score * 0.05, 1),
            "claude":     round(overall * 0.96 + cit_score * 0.04, 1),
            "perplexity": round(overall * 0.92 + struct_score * 0.08, 1),
            "bing-chat":  round(overall * 0.90, 1),
        }

    @staticmethod
    def _identify_strengths(e: float, c: float, s: float, st: float) -> list[str]:
        strengths = []
        if e >= 18:
            strengths.append("Strong entity coverage — well-mapped named entities.")
        if c >= 18:
            strengths.append("High citation readiness — statistics and sources present.")
        if s >= 18:
            strengths.append("Semantically rich content — good factual density.")
        if st >= 18:
            strengths.append("Well-structured content — headings and lists aid AI extraction.")
        return strengths

    @staticmethod
    def _identify_improvements(
        e: float, c: float, s: float, st: float, word_count: int
    ) -> list[str]:
        improvements = []
        if e < 15:
            improvements.append("Increase entity density — add named organisations, people, or technologies.")
        if c < 15:
            improvements.append("Improve citation readiness — include statistics, dates, and source attributions.")
        if s < 15:
            improvements.append("Increase semantic clarity — use direct, unambiguous factual statements.")
        if st < 15:
            improvements.append("Improve structure — add headings (H2/H3) and bullet lists.")
        if word_count < 500:
            improvements.append("Content is short — AI models prefer 500+ words for reliable extraction.")
        return improvements
