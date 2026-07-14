"""
DATA ENGINE — Embedding Pipeline
Orchestrates provider selection, batching, fallback, and metrics.
Phase 1: Core pipeline for converting chunks to vectors.

Namespace isolation: each embedding model writes into a dedicated
vector column (embedding_768, embedding_1536, etc.) to prevent
cross-model cosine distance calculations which produce meaningless scores.
"""

import asyncio
import time
import structlog
from typing import Protocol

from configs.settings import get_settings
from configs.constants import EmbeddingProvider, EMBEDDING_MODELS
from shared.exceptions.base import EmbeddingProviderError

logger = structlog.get_logger(__name__)
settings = get_settings()

# Maps dimension count → database column name
DIMENSION_TO_COLUMN: dict[int, str] = {
    768:  "embedding_768",
    1536: "embedding_1536",
    1024: "embedding_1024",
    3072: "embedding_3072",
}


def get_vector_column(provider: str, model: str) -> str:
    """
    Return the correct vector namespace column for a given provider/model.
    Raises ValueError if the model is not registered in EMBEDDING_MODELS.
    """
    provider_models = EMBEDDING_MODELS.get(provider, {})
    dims = provider_models.get(model)
    if dims is None:
        raise ValueError(
            f"Unknown model '{model}' for provider '{provider}'. "
            f"Register it in configs/constants.py EMBEDDING_MODELS."
        )
    col = DIMENSION_TO_COLUMN.get(dims)
    if col is None:
        raise ValueError(
            f"No namespace column defined for {dims} dimensions. "
            f"Add 'embedding_{dims}' column to the embeddings table."
        )
    return col


class EmbeddingProviderProtocol(Protocol):
    """Interface that all embedding providers must satisfy."""

    async def embed_batched(self, texts: list[str], batch_size: int) -> list[list[float]]: ...

    @property
    def provider_name(self) -> str: ...

    @property
    def embedding_dimensions(self) -> int: ...


class EmbeddingPipeline:
    """
    Manages provider selection, batching, and fallback chain.

    Usage:
        pipeline = EmbeddingPipeline()
        vectors = await pipeline.embed(texts)
    """

    def __init__(
        self,
        primary_provider: str | None = None,
        fallback_providers: list[str] | None = None,
    ) -> None:
        self._primary = primary_provider or settings.default_embedding_provider
        self._fallback_chain = fallback_providers or self._default_fallback_chain()
        self._providers: dict[str, EmbeddingProviderProtocol] = {}

    def _default_fallback_chain(self) -> list[str]:
        """Build fallback order excluding the primary provider."""
        all_providers = [
            EmbeddingProvider.OPENAI,
            EmbeddingProvider.VOYAGE,
            EmbeddingProvider.GEMINI,
            EmbeddingProvider.JINA,
        ]
        return [p for p in all_providers if p != self._primary]

    def _get_provider(self, name: str) -> EmbeddingProviderProtocol:
        """Lazily instantiate and cache provider instances."""
        if name not in self._providers:
            self._providers[name] = self._create_provider(name)
        return self._providers[name]

    @staticmethod
    def _create_provider(name: str) -> EmbeddingProviderProtocol:
        """Factory: create provider by name."""
        if name == EmbeddingProvider.OPENAI:
            from services.embedding_service.providers.openai import OpenAIEmbeddingProvider
            return OpenAIEmbeddingProvider()
        elif name == EmbeddingProvider.GEMINI:
            from services.embedding_service.providers.gemini import GeminiEmbeddingProvider
            return GeminiEmbeddingProvider()
        elif name == EmbeddingProvider.VOYAGE:
            from services.embedding_service.providers.voyage import VoyageEmbeddingProvider
            return VoyageEmbeddingProvider()
        elif name == EmbeddingProvider.JINA:
            from services.embedding_service.providers.jina import JinaEmbeddingProvider
            return JinaEmbeddingProvider()
        else:
            raise ValueError(f"Unknown embedding provider: {name}")

    async def embed(
        self,
        texts: list[str],
        provider: str | None = None,
        batch_size: int | None = None,
    ) -> tuple[list[list[float]], str, str]:
        """
        Embed texts with automatic fallback.

        Returns:
            Tuple of (embeddings, provider_name, model_name)

        Raises:
            EmbeddingProviderError: If all providers fail.
        """
        if not texts:
            return [], self._primary, ""

        bs = batch_size or settings.embedding_batch_size
        provider_order = [provider or self._primary] + self._fallback_chain

        last_error: Exception | None = None
        for provider_name in provider_order:
            try:
                p = self._get_provider(provider_name)
                start = time.perf_counter()
                embeddings = await p.embed_batched(texts, batch_size=bs)
                duration = time.perf_counter() - start

                logger.info(
                    "embeddings_generated",
                    provider=provider_name,
                    count=len(embeddings),
                    duration_ms=round(duration * 1000, 2),
                    model=settings.default_embedding_model,
                    vector_column=get_vector_column(provider_name, settings.default_embedding_model),
                )
                return embeddings, p.provider_name, settings.default_embedding_model

            except EmbeddingProviderError as exc:
                logger.warning(
                    "embedding_provider_failed_trying_fallback",
                    provider=provider_name,
                    error=str(exc),
                )
                last_error = exc
                continue

        raise EmbeddingProviderError(
            f"All embedding providers failed. Last error: {last_error}"
        )

    @property
    def primary_provider(self) -> str:
        return self._primary
