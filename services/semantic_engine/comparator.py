"""
DATA ENGINE — Semantic Comparator
High-level interface for comparing documents, chunks, and trend signals.
Phase 2: Measures relationships between datasets for gap detection input.
"""

import structlog
from dataclasses import dataclass

from services.semantic_engine.cosine_similarity import (
    cosine_similarity,
    average_vector,
    pairwise_cosine_similarity,
)

logger = structlog.get_logger(__name__)


@dataclass
class ComparisonResult:
    """Result of a semantic comparison operation."""
    entity_a_id: str
    entity_b_id: str
    similarity: float
    distance: float
    interpretation: str


class SemanticComparator:
    """
    Compares documents and vector sets to measure semantic overlap.
    Used by gap detection to identify coverage deficiencies.
    """

    INTERPRETATION_THRESHOLDS = [
        (0.95, "near-duplicate"),
        (0.85, "highly similar"),
        (0.70, "semantically related"),
        (0.50, "loosely related"),
        (0.0,  "semantically distinct"),
    ]

    def compare_vectors(
        self, id_a: str, vec_a: list[float], id_b: str, vec_b: list[float]
    ) -> ComparisonResult:
        """Compare two individual vectors."""
        sim = cosine_similarity(vec_a, vec_b)
        return ComparisonResult(
            entity_a_id=id_a,
            entity_b_id=id_b,
            similarity=round(sim, 6),
            distance=round(1.0 - sim, 6),
            interpretation=self._interpret(sim),
        )

    def compare_document_sets(
        self,
        doc_a_id: str,
        doc_a_vectors: list[list[float]],
        doc_b_id: str,
        doc_b_vectors: list[list[float]],
    ) -> ComparisonResult:
        """
        Compare two documents by their average embedding vectors.
        Gives a coarse-grained semantic similarity between full documents.
        """
        if not doc_a_vectors or not doc_b_vectors:
            return ComparisonResult(
                entity_a_id=doc_a_id,
                entity_b_id=doc_b_id,
                similarity=0.0,
                distance=1.0,
                interpretation="insufficient data",
            )

        avg_a = average_vector(doc_a_vectors)
        avg_b = average_vector(doc_b_vectors)
        return self.compare_vectors(doc_a_id, avg_a, doc_b_id, avg_b)

    def coverage_matrix(
        self,
        document_vectors: list[list[float]],
        trend_vectors: list[list[float]],
    ) -> list[list[float]]:
        """
        Compute pairwise similarity between document chunks and trend signals.
        Returns an (n_doc_chunks × n_trends) similarity matrix.
        Used by gap detection to identify uncovered trend topics.
        """
        import numpy as np
        matrix = pairwise_cosine_similarity(document_vectors, trend_vectors)
        return matrix.tolist()

    def average_coverage_score(
        self,
        document_vectors: list[list[float]],
        trend_vectors: list[list[float]],
        threshold: float = 0.70,
    ) -> float:
        """
        Compute what fraction of trends are 'covered' by the document.
        A trend is considered covered if at least one document chunk
        has similarity >= threshold with it.

        Returns:
            Coverage ratio in [0.0, 1.0].
        """
        if not document_vectors or not trend_vectors:
            return 0.0

        import numpy as np
        matrix = pairwise_cosine_similarity(document_vectors, trend_vectors)
        # For each trend (column), take the max similarity across all doc chunks (rows)
        max_per_trend = matrix.max(axis=0)
        covered = (max_per_trend >= threshold).sum()
        return float(covered / len(trend_vectors))

    def _interpret(self, similarity: float) -> str:
        for threshold, label in self.INTERPRETATION_THRESHOLDS:
            if similarity >= threshold:
                return label
        return "semantically distinct"
