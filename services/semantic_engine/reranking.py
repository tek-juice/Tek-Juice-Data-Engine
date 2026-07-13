"""
DATA ENGINE — Cross-Encoder Reranking
Secondary reranking pass using a cross-encoder model for higher precision.
Applied after initial ANN retrieval to improve top-k quality.
Phase 4: Production precision enhancement.
"""

import structlog
from dataclasses import dataclass

logger = structlog.get_logger(__name__)


@dataclass
class RerankResult:
    id: str
    text: str
    original_rank: int
    rerank_score: float
    final_rank: int


class CrossEncoderReranker:
    """
    Reranks retrieval results using a cross-encoder model.
    Cross-encoders evaluate (query, document) pairs jointly for higher
    precision than bi-encoder cosine similarity alone.

    Default model: cross-encoder/ms-marco-MiniLM-L-6-v2
    (small, fast, good general-purpose reranker)
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        self._model_name = model_name
        self._model = None  # Lazy-loaded

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self._model_name)
                logger.info("cross_encoder_loaded", model=self._model_name)
            except ImportError:
                logger.warning("sentence_transformers_not_available_skipping_rerank")
        return self._model

    def rerank(
        self,
        query: str,
        results: list[dict],
        top_k: int | None = None,
    ) -> list[RerankResult]:
        """
        Rerank results using cross-encoder scores.

        Args:
            query:   Original query text.
            results: List of dicts with keys: id, text (and optional metadata).
            top_k:   Limit output to top_k results.

        Returns:
            List of RerankResult sorted by rerank_score descending.
        """
        model = self._load_model()
        if model is None or not results:
            # Fall back to original order
            return [
                RerankResult(
                    id=r["id"],
                    text=r.get("text", ""),
                    original_rank=i,
                    rerank_score=r.get("similarity", 0.0),
                    final_rank=i,
                )
                for i, r in enumerate(results)
            ]

        pairs = [(query, r.get("text", "")) for r in results]
        scores = model.predict(pairs)

        reranked = sorted(
            zip(results, scores),
            key=lambda x: x[1],
            reverse=True,
        )

        rerank_results = [
            RerankResult(
                id=r["id"],
                text=r.get("text", ""),
                original_rank=i,
                rerank_score=round(float(score), 6),
                final_rank=j,
            )
            for j, (i, (r, score)) in enumerate(
                ((i, item) for i, item in enumerate(reranked))
            )
        ]

        logger.debug("reranking_complete", input=len(results), output=len(rerank_results))
        return rerank_results[:top_k] if top_k else rerank_results
