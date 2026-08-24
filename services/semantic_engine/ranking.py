"""
DATA ENGINE — Semantic Ranking
Ranks search results and candidate content by relevance using vector similarity
and optional metadata boosting.
Phase 2: Semantic matching and pattern analysis.
"""

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import structlog

from services.semantic_engine.cosine_similarity import cosine_similarity

logger = structlog.get_logger(__name__)


@dataclass
class RankedResult:
    """A single ranked item with score breakdown."""
    id: str
    text: str
    similarity_score: float
    boost_score: float = 0.0
    final_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class SemanticRanker:
    """
    Ranks candidates by cosine similarity to a query vector.
    Supports optional metadata-based boosting (e.g. recency, authority).
    """

    def __init__(self, boost_weight: float = 0.1) -> None:
        """
        Args:
            boost_weight: Weight applied to boost signals (0.0 = pure similarity).
        """
        self.boost_weight = boost_weight

    def rank(
        self,
        query_vector: list[float],
        candidates: list[dict],
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> list[RankedResult]:
        """
        Rank a list of candidate items against a query vector.

        Args:
            query_vector: Query embedding.
            candidates:   List of dicts with keys: id, text, embedding, metadata (optional).
            top_k:        Maximum results to return.
            threshold:    Minimum similarity threshold.

        Returns:
            List of RankedResult sorted by final_score descending.
        """
        results: list[RankedResult] = []

        for item in candidates:
            sim = cosine_similarity(query_vector, item["embedding"])
            if sim < threshold:
                continue

            boost = self._compute_boost(item.get("metadata", {}))
            final = (1.0 - self.boost_weight) * sim + self.boost_weight * boost

            results.append(
                RankedResult(
                    id=item["id"],
                    text=item.get("text", ""),
                    similarity_score=round(sim, 6),
                    boost_score=round(boost, 6),
                    final_score=round(final, 6),
                    metadata=item.get("metadata", {}),
                )
            )

        results.sort(key=lambda r: r.final_score, reverse=True)
        logger.debug("semantic_ranking_complete", total=len(results), top_k=top_k)
        return results[:top_k]

    def _compute_boost(self, metadata: dict) -> float:
        """
        Compute a normalised boost signal from metadata.
        Currently supports:
          - relevance_score (0.0–1.0)
          - recency (points / max_points normalised)
        """
        boost = 0.0
        if "relevance_score" in metadata:
            boost = max(0.0, min(1.0, float(metadata["relevance_score"])))
        elif "points" in metadata:
            boost = min(1.0, (metadata.get("points", 0) or 0) / 1000)
        return boost
