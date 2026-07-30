"""
DATA ENGINE — GEO LLM Visibility Scorer
Phase 4: Measures how discoverable and citable content is
across different generative AI engines.

Each engine has different citation preferences — this scorer applies
per-engine weight multipliers so the overall score reflects what actually
gets cited first on each platform.

Engine citation preference matrix:
  Google AI Overviews: entity sameAs (30%), E-E-A-T/date (25%), schema (25%), structure (20%)
  Perplexity:          citation readiness (35%), freshness (25%), structure (25%), entity (15%)
  ChatGPT:             semantic clarity (30%), entity authority (25%), structure (25%), citation (20%)
  Bing Copilot:        structure/snippets (35%), citation (30%), entity (20%), clarity (15%)
  Claude:              semantic clarity (40%), factual density (30%), structure (20%), entity (10%)
  Gemini:              schema/entity (35%), E-E-A-T (25%), citation (25%), structure (15%)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class LLMVisibilityScore:
    """Comprehensive LLM visibility assessment for a document."""
    overall_score: float              # 0–100 weighted composite
    entity_coverage_score: float      # 0–25
    citation_readiness_score: float   # 0–25
    semantic_clarity_score: float     # 0–25
    structure_score: float            # 0–25
    target_models: list[str]
    model_specific_scores: dict[str, float]  # per-engine precision scores
    strengths: list[str]
    improvements: list[str]
    first_sentence_compliant: bool = False
    has_stats: bool = False
    has_lists: bool = False
    optimised_excerpt: str | None = None


SUPPORTED_MODELS = [
    "google_ai_overviews", "perplexity", "chatgpt",
    "bing_copilot", "claude", "gemini",
]

# Per-engine dimension weights — must sum to 1.0 per engine
_ENGINE_WEIGHTS: dict[str, dict[str, float]] = {
    "google_ai_overviews": {"entity": 0.30, "citation": 0.25, "structure": 0.25, "clarity": 0.20},
    "perplexity":          {"citation": 0.35, "entity": 0.15, "structure": 0.25, "clarity": 0.25},
    "chatgpt":             {"clarity": 0.30, "entity": 0.25, "structure": 0.25, "citation": 0.20},
    "bing_copilot":        {"structure": 0.35, "citation": 0.30, "entity": 0.20, "clarity": 0.15},
    "claude":              {"clarity": 0.40, "citation": 0.30, "structure": 0.20, "entity": 0.10},
    "gemini":              {"entity": 0.35, "citation": 0.25, "structure": 0.15, "clarity": 0.25},
}

# Bonus points for signals that specific engines prioritise
_ENGINE_BONUSES: dict[str, list[tuple[str, float]]] = {
    # (signal_name, bonus_pts)
    "google_ai_overviews": [("same_as", 5.0), ("date_present", 3.0), ("faq_schema", 4.0)],
    "perplexity":          [("has_stats", 5.0), ("has_sources", 5.0), ("has_date", 3.0)],
    "chatgpt":             [("direct_first_sentence", 5.0), ("has_lists", 3.0)],
    "bing_copilot":        [("has_lists", 5.0), ("has_steps", 4.0), ("direct_first_sentence", 3.0)],
    "claude":              [("direct_first_sentence", 6.0), ("no_passive", 3.0)],
    "gemini":              [("same_as", 6.0), ("faq_schema", 4.0), ("date_present", 2.0)],
}


class LLMVisibilityScorer:
    """
    Scores content for visibility in generative AI search responses.

    Scoring dimensions (25 pts each = 100 base total):
    1. Entity Coverage    — named entities, density, Wikidata links
    2. Citation Readiness — statistics, sources, dates, quotes
    3. Semantic Clarity   — unambiguous direct factual statements
    4. Content Structure  — headings, lists, paragraph length

    Per-engine bonus points (up to +20 pts) based on each engine's
    known citation preference signals (sameAs, FAQ schema, stats, etc.).
    Final scores are capped at 100.
    """

    def score(
        self,
        content: str,
        entity_count: int = 0,
        citation_score: float = 0.0,
        context_richness: float = 0.0,
        target_models: list[str] | None = None,
        has_same_as: bool = False,
        has_faq_schema: bool = False,
    ) -> LLMVisibilityScore:
        """
        Compute LLM visibility score with per-engine precision weighting.

        Args:
            content:          Plain text content.
            entity_count:     Number of named entities (from EntityMapper).
            citation_score:   Citation readiness score 0–100 (from CitationReadinessAnalyser).
            context_richness: Context richness 0–100 (from SemanticOptimiser).
            target_models:    AI engines to score for. Defaults to all 6.
            has_same_as:      True if verified sameAs links are present in schema.
            has_faq_schema:   True if FAQPage schema is present.

        Returns:
            LLMVisibilityScore with per-engine breakdown.
        """
        models = target_models or SUPPORTED_MODELS
        word_count = len(content.split())

        # ── Base dimension scores (0–25 each) ────────────────────────────────

        # Dimension 1: Entity Coverage
        entity_density = entity_count / max(word_count, 1) * 100
        entity_score = min(25.0, entity_density * 2.5)

        # Dimension 2: Citation Readiness
        cit_score = min(25.0, citation_score * 0.25)

        # Dimension 3: Semantic Clarity
        sem_score = self._score_semantic_clarity(content, context_richness)

        # Dimension 4: Structure
        struct_score, has_lists, has_steps = self._score_structure(content)

        # ── Content signal flags ──────────────────────────────────────────────
        has_stats = bool(re.search(
            r'\b\d+(?:\.\d+)?(?:%|x|times|k|m|b|million|billion|\s?percent)\b',
            content, re.IGNORECASE
        ))
        has_sources = bool(re.search(
            r'\b(?:according to|source:|cited from|study by|data from|research by|published in)\b',
            content, re.IGNORECASE
        ))
        has_date = bool(re.search(r'\b(20\d{2}|january|february|march|april|may|june|july|august|september|october|november|december)\b', content, re.IGNORECASE))
        first_sentence = content.strip().split('.')[0] if content else ""
        direct_first = self._is_direct_first_sentence(first_sentence)
        has_passive = bool(re.search(r'\b(?:is|are|was|were|been)\s+\w+ed\b', content[:500], re.IGNORECASE))

        signals: dict[str, bool] = {
            "same_as":              has_same_as,
            "date_present":         has_date,
            "faq_schema":           has_faq_schema,
            "has_stats":            has_stats,
            "has_sources":          has_sources,
            "has_date":             has_date,
            "direct_first_sentence": direct_first,
            "has_lists":            has_lists,
            "has_steps":            has_steps,
            "no_passive":           not has_passive,
        }

        # ── Per-engine weighted scores ────────────────────────────────────────
        model_scores: dict[str, float] = {}
        for engine in models:
            if engine not in _ENGINE_WEIGHTS:
                # Unknown engine — use base average
                model_scores[engine] = round(
                    (entity_score + cit_score + sem_score + struct_score), 1
                )
                continue

            weights = _ENGINE_WEIGHTS[engine]
            dim_map = {
                "entity":   entity_score * 4,   # scale 0-25 → 0-100 for weighting
                "citation": cit_score * 4,
                "clarity":  sem_score * 4,
                "structure": struct_score * 4,
            }
            weighted = sum(
                dim_map.get(dim, 0) * w for dim, w in weights.items()
            )

            # Apply engine-specific bonuses
            bonus = 0.0
            for signal_name, bonus_pts in _ENGINE_BONUSES.get(engine, []):
                if signals.get(signal_name):
                    bonus += bonus_pts

            model_scores[engine] = round(min(100.0, weighted + bonus), 1)

        # Overall score = average of all targeted engine scores
        overall = sum(model_scores.values()) / max(len(model_scores), 1)

        strengths  = self._identify_strengths(entity_score, cit_score, sem_score, struct_score, signals)
        improvements = self._identify_improvements(entity_score, cit_score, sem_score, struct_score, word_count, signals)

        logger.debug(
            "llm_visibility_scored",
            overall=round(overall, 1),
            entity=round(entity_score, 1),
            citation=round(cit_score, 1),
            structure=round(struct_score, 1),
            has_same_as=has_same_as,
            has_stats=has_stats,
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
            first_sentence_compliant=direct_first,
            has_stats=has_stats,
            has_lists=has_lists,
        )

    @staticmethod
    def _score_semantic_clarity(content: str, context_richness: float) -> float:
        """
        Score semantic clarity — unambiguous, direct, factual content.
        Combines the SemanticOptimiser context_richness score with
        direct-statement signals that AI engines prefer.
        """
        base = min(15.0, context_richness * 0.15)

        # Bonus for direct factual patterns
        direct_patterns = [
            r'\b(?:is defined as|refers to|means that|is a type of)\b',
            r'\b(?:provides|enables|allows|delivers|reduces|increases)\b',
            r'\b\d+(?:\.\d+)?(?:%|x|times)\b',
        ]
        bonus = 0.0
        for p in direct_patterns:
            if re.search(p, content, re.IGNORECASE):
                bonus += 2.5
        return min(25.0, base + bonus)

    @staticmethod
    def _score_structure(content: str) -> tuple[float, bool, bool]:
        """Score structure. Returns (score, has_lists, has_steps)."""
        score = 0.0
        has_headings = bool(re.search(r'^#{1,3}\s+', content, re.MULTILINE))
        has_lists    = bool(re.search(r'^\s*[-*•]\s+', content, re.MULTILINE))
        has_steps    = bool(re.search(r'^\s*\d+[.)]\s+', content, re.MULTILINE))
        has_table    = bool(re.search(r'^\|.+\|', content, re.MULTILINE))

        if has_headings:  score += 7.0
        if has_lists:     score += 7.0
        if has_steps:     score += 5.0
        if has_table:     score += 3.0

        paragraphs = [p for p in content.split('\n\n') if p.strip()]
        if paragraphs:
            avg_len = sum(len(p.split()) for p in paragraphs) / len(paragraphs)
            if 40 <= avg_len <= 150:
                score += 3.0

        return min(25.0, score), has_lists, has_steps

    @staticmethod
    def _is_direct_first_sentence(sentence: str) -> bool:
        """Check if first sentence follows the First Sentence Rule."""
        if not sentence or len(sentence) < 20:
            return False
        non_compliant = ("in this", "this article", "we will", "today",
                         "there are", "it is", "many people", "welcome")
        lower = sentence.lower().strip()
        return not any(lower.startswith(s) for s in non_compliant)

    @staticmethod
    def _identify_strengths(
        e: float, c: float, s: float, st: float, signals: dict
    ) -> list[str]:
        strengths = []
        if e >= 18:
            strengths.append("Strong entity coverage — well-mapped named entities boost all AI engines.")
        if c >= 18:
            strengths.append("High citation readiness — statistics and sources present.")
        if s >= 18:
            strengths.append("Semantically rich — good factual density for Claude and ChatGPT.")
        if st >= 18:
            strengths.append("Well-structured — headings and lists aid AI extraction for Bing Copilot.")
        if signals.get("same_as"):
            strengths.append("Verified sameAs links present — boosts Google AI Overviews and Gemini.")
        if signals.get("has_stats"):
            strengths.append("Statistics present — strong signal for Perplexity citations.")
        if signals.get("faq_schema"):
            strengths.append("FAQ schema present — highest-value schema for AI citation.")
        return strengths

    @staticmethod
    def _identify_improvements(
        e: float, c: float, s: float, st: float,
        word_count: int, signals: dict,
    ) -> list[str]:
        improvements = []
        if e < 15:
            improvements.append("Add named organisations, people, or technologies with full context.")
        if c < 15:
            improvements.append("Add statistics, dates, and source attributions for Perplexity and ChatGPT.")
        if s < 15:
            improvements.append("Use direct, unambiguous factual statements — critical for Claude.")
        if st < 15:
            improvements.append("Add headings (H2/H3) and bullet lists — critical for Bing Copilot featured snippets.")
        if not signals.get("same_as"):
            improvements.append("Add verified sameAs links (Wikidata/Wikipedia) — #1 signal for Google AI Overviews.")
        if not signals.get("has_stats"):
            improvements.append("Include measurable statistics — Perplexity cites content with numbers 3x more often.")
        if not signals.get("direct_first_sentence"):
            improvements.append("Fix first sentence — must open with a direct factual answer, not a preamble.")
        if not signals.get("faq_schema"):
            improvements.append("Consider FAQPage schema — highest citation trigger for Google AI Overviews.")
        if word_count < 500:
            improvements.append("Content is too short — AI engines prefer 500+ words for reliable extraction.")
        return improvements
