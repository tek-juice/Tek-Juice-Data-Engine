"""
DATA ENGINE — Voyage AI Embedding Provider
Generates embeddings via the Voyage AI API.
"""

import structlog
import voyageai
from tenacity import retry, stop_after_attempt, wait_exponential

from configs.settings import get_settings
from shared.exceptions.base import EmbeddingProviderError

logger = structlog.get_logger(__name__)
settings = get_settings()


class VoyageEmbeddingProvider:
    """Voyage AI embedding provider with batch support."""

    SUPPORTED_MODELS = {
        "voyage-large-2": 1536,
        "voyage-2": 1024,
        "voyage-code-2": 1536,
    }

    def __init__(self, model: str = "voyage-large-2") -> None:
        self.model = model
        self.dimensions = self.SUPPORTED_MODELS.get(model, 1536)
        self._client = voyageai.Client(api_key=settings.voyage_api_key)

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60), reraise=True)
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings via Voyage AI."""
        if not texts:
            return []
        try:
            result = self._client.embed(texts, model=self.model, input_type="document")
            logger.debug("voyage_embeddings_generated", count=len(result.embeddings), model=self.model)
            return result.embeddings
        except Exception as exc:
            logger.error("voyage_api_error", error=str(exc))
            raise EmbeddingProviderError(f"Voyage API error: {exc}") from exc

    async def embed_batched(self, texts: list[str], batch_size: int = 128) -> list[list[float]]:
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            all_embeddings.extend(await self.embed(texts[i : i + batch_size]))
        return all_embeddings

    @property
    def provider_name(self) -> str:
        return "voyage"

    @property
    def embedding_dimensions(self) -> int:
        return self.dimensions
