"""
DATA ENGINE — GEO Semantic Optimiser
Phase 4: Optimises content structure for maximum AI/LLM discoverability.
Implements context enrichment, entity co-reference resolution,
and semantic density improvements for generative search engines.
"""

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SemanticOptimisationResult:
    """Result of semantic optimisation for GEO."""
    original_word_count: int
    optimised_word_count: int
    entity_density_before: float
    entity_density_after: float
    context_richness_score: float   # 0.0–100.0
    changes_made: list[str]
    optimised_content: str
    recommendations: list[str]


class GEOSemanticOptimiser:
    """
    Optimises content for Generative Engine Optimisation (GEO).

    GEO focuses on making content easily extractable and citable
    by AI systems such as ChatGPT, Gemini, Perplexity, and Claude.

    Key optimisation principles:
    1. Entity clarity — unambiguous named entities with full context
    2. Fact density — clear, citable factual statements
    3. Structure — well-organised sections that AI can excerpt
    4. Citation readiness — quotes, statistics, and sourced claims
    5. Semantic completeness — covers all aspects of the topic
    """

    def optimise(
        self,
        content: str,
        entities: list[dict],
        target_topics: list[str] | None = None,
    ) -> SemanticOptimisationResult:
        """
        Apply GEO semantic optimisations to content.

        Args:
            content:       Original plain text content.
            entities:      List of extracted entity dicts (from EntityMapper).
            target_topics: Topics to ensure coverage of.

        Returns:
            SemanticOptimisationResult with improved content and metrics.
        """
        original_words = len(content.split())
        entity_density_before = len(entities) / original_words if original_words > 0 else 0.0

        changes: list[str] = []
        optimised = content

        # Step 1: Add context to ambiguous entity references
        optimised, step_changes = self._clarify_entity_references(optimised, entities)
        changes.extend(step_changes)

        # Step 2: Ensure topic sentences at section starts
        optimised, step_changes = self._add_topic_sentences(optimised, target_topics or [])
        changes.extend(step_changes)

        # Step 3: Convert passive to active voice (heuristic)
        optimised, step_changes = self._flag_passive_constructs(optimised)
        changes.extend(step_changes)

        optimised_words = len(optimised.split())
        entity_density_after = len(entities) / optimised_words if optimised_words > 0 else 0.0

        context_score = self._compute_context_richness(optimised, entities)
        recs = self._generate_recommendations(content, entities, target_topics or [])

        return SemanticOptimisationResult(
            original_word_count=original_words,
            optimised_word_count=optimised_words,
            entity_density_before=round(entity_density_before, 4),
            entity_density_after=round(entity_density_after, 4),
            context_richness_score=round(context_score, 1),
            changes_made=changes,
            optimised_content=optimised,
            recommendations=recs,
        )

    def _clarify_entity_references(
        self, content: str, entities: list[dict]
    ) -> tuple[str, list[str]]:
        """Add type clarifiers after first occurrence of key entities."""
        changes = []
        import re
        for entity in entities[:10]:  # limit to top 10 entities
            text = entity.get("text", "")
            etype = entity.get("entity_type", "")
            if not text or not etype or etype == "Concept":
                continue
            # Only clarify first occurrence if it lacks a parenthetical
            pattern = rf'\b{re.escape(text)}\b(?!\s*\()'
            if re.search(pattern, content):
                replacement = f"{text} (the {etype.lower()})"
                content = re.sub(pattern, replacement, content, count=1)
                changes.append(f"Added type context to entity: '{text}'")
        return content, changes

    def _add_topic_sentences(
        self, content: str, topics: list[str]
    ) -> tuple[str, list[str]]:
        """Prepend a clear topic sentence if key topics are buried."""
        changes = []
        for topic in topics[:3]:
            if topic.lower() not in content[:200].lower() and topic.lower() in content.lower():
                sentence = f"This content covers {topic}. "
                content = sentence + content
                changes.append(f"Added topic sentence for: '{topic}'")
        return content, changes

    def _flag_passive_constructs(self, content: str) -> tuple[str, list[str]]:
        """Flag passive voice patterns as recommendations (not auto-changed)."""
        import re
        passive_patterns = [
            r'\b(?:is|are|was|were|been|being)\s+\w+ed\b',
        ]
        changes = []
        for pattern in passive_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if len(matches) > 3:
                changes.append(
                    f"Found {len(matches)} passive voice constructs — "
                    "consider converting to active voice for AI extractability."
                )
                break
        return content, changes

    def _compute_context_richness(self, content: str, entities: list[dict]) -> float:
        """
        Score the semantic richness of content on a 0–100 scale.
        Based on entity density, sentence variety, and factual markers.
        """
        import re
        word_count = len(content.split())
        if word_count < 50:
            return 10.0

        entity_score = min(40.0, len(entities) / word_count * 4000)

        # Factual markers (numbers, dates, statistics)
        facts = re.findall(r'\b\d+(?:\.\d+)?(?:%|x|\s?times)?\b', content)
        fact_score = min(30.0, len(facts) * 2.0)

        # Sentence variety (avg words per sentence)
        sentences = re.split(r'[.!?]+', content)
        avg_sent_len = sum(len(s.split()) for s in sentences) / max(len(sentences), 1)
        variety_score = min(30.0, 30.0 * (1.0 - abs(avg_sent_len - 18) / 20))

        return round(entity_score + fact_score + variety_score, 1)

    def _generate_recommendations(
        self, content: str, entities: list[dict], topics: list[str]
    ) -> list[str]:
        recs = []
        word_count = len(content.split())

        if word_count < 300:
            recs.append("Content is short (<300 words) — expand for better AI coverage.")

        if len(entities) < 5:
            recs.append("Few named entities found — add specific names, organisations, or technologies.")

        for topic in topics[:3]:
            if topic.lower() not in content.lower():
                recs.append(f"Topic '{topic}' not covered — add a dedicated section.")

        if not any(c.isdigit() for c in content):
            recs.append("No statistics or numbers found — add measurable facts for citation readiness.")

        return recs
