"""
DATA ENGINE — OpenAI Embedding Provider
Generates embeddings via the OpenAI Embeddings API with retry and batching.
"""

import asyncio
from typing import Any

import structlog
from openai import AsyncOpenAI, RateLimitError, APIError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from configs.settings import get_settings
from shared.exceptions.base import EmbeddingProviderError

logger = structlog.get_logger(__name__)
settings = get_settings()


class OpenAIEmbeddingProvider:
    """
    Async OpenAI embedding provider.
    Supports batched requests with exponential backoff on rate limits.
    """

    SUPPORTED_MODELS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.default_embedding_model
        self.dimensions = self.SUPPORTED_MODELS.get(self.model, settings.embedding_dimension)
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        reraise=True,
    )
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (one per input text).

        Raises:
            EmbeddingProviderError: On API failure after retries.
        """
        if not texts:
            return []

        try:
            response = await self._client.embeddings.create(
                input=texts,
                model=self.model,
            )
            embeddings = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
            logger.debug(
                "openai_embeddings_generated",
                count=len(embeddings),
                model=self.model,
                total_tokens=response.usage.total_tokens,
            )
            return embeddings

        except RateLimitError:
            logger.warning("openai_rate_limit_hit", model=self.model)
            raise
        except APIError as exc:
            logger.error("openai_api_error", error=str(exc), model=self.model)
            raise EmbeddingProviderError(f"OpenAI API error: {exc}") from exc

    async def embed_batched(
        self, texts: list[str], batch_size: int | None = None
    ) -> list[list[float]]:
        """Embed texts in batches to respect API limits."""
        bs = batch_size or settings.embedding_batch_size
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), bs):
            batch = texts[i : i + bs]
            batch_embeddings = await self.embed(batch)
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def embedding_dimensions(self) -> int:
        return self.dimensions
