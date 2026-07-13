"""
DATA ENGINE — Jina AI Embedding Provider
Generates embeddings via the Jina Embeddings API.
"""

import structlog
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from configs.settings import get_settings
from shared.exceptions.base import EmbeddingProviderError

logger = structlog.get_logger(__name__)
settings = get_settings()

JINA_EMBED_URL = "https://api.jina.ai/v1/embeddings"


class JinaEmbeddingProvider:
    """Jina AI embedding provider via HTTP API."""

    SUPPORTED_MODELS = {
        "jina-embeddings-v2-base-en": 768,
        "jina-embeddings-v2-small-en": 512,
    }

    def __init__(self, model: str = "jina-embeddings-v2-base-en") -> None:
        self.model = model
        self.dimensions = self.SUPPORTED_MODELS.get(model, 768)
        self._headers = {
            "Authorization": f"Bearer {settings.jina_api_key}",
            "Content-Type": "application/json",
        }

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60), reraise=True)
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings via Jina API."""
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    JINA_EMBED_URL,
                    headers=self._headers,
                    json={"input": texts, "model": self.model},
                )
                response.raise_for_status()
                data = response.json()
                embeddings = [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
                logger.debug("jina_embeddings_generated", count=len(embeddings), model=self.model)
                return embeddings
        except httpx.HTTPStatusError as exc:
            logger.error("jina_http_error", status=exc.response.status_code)
            raise EmbeddingProviderError(f"Jina API HTTP error: {exc}") from exc
        except Exception as exc:
            logger.error("jina_api_error", error=str(exc))
            raise EmbeddingProviderError(f"Jina API error: {exc}") from exc

    async def embed_batched(self, texts: list[str], batch_size: int = 100) -> list[list[float]]:
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            all_embeddings.extend(await self.embed(texts[i : i + batch_size]))
        return all_embeddings

    @property
    def provider_name(self) -> str:
        return "jina"

    @property
    def embedding_dimensions(self) -> int:
        return self.dimensions
