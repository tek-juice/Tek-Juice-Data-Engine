"""
DATA ENGINE — Gemini Embedding Provider
Generates embeddings via the Google Generative AI Embeddings API.
"""

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

import google.generativeai as genai
from configs.settings import get_settings
from shared.exceptions.base import EmbeddingProviderError

logger = structlog.get_logger(__name__)
settings = get_settings()


class GeminiEmbeddingProvider:
    """Async-compatible Google Gemini embedding provider."""

    SUPPORTED_MODELS = {
        "embedding-001": 768,
        "text-embedding-004": 768,
    }

    def __init__(self, model: str | None = None) -> None:
        # Strip the "models/" prefix that settings stores for API routing purposes —
        # SUPPORTED_MODELS keys use bare names ("text-embedding-004").
        raw = model or settings.default_embedding_model
        self.model = raw.removeprefix("models/")
        self.dimensions = self.SUPPORTED_MODELS.get(self.model, 768)
        genai.configure(api_key=settings.gemini_api_key)

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60), reraise=True)
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using Gemini embedding model."""
        if not texts:
            return []
        try:
            embeddings = []
            for text in texts:
                result = genai.embed_content(
                    model=f"models/{self.model}",
                    content=text,
                    task_type="retrieval_document",
                )
                embeddings.append(result["embedding"])

            logger.debug("gemini_embeddings_generated", count=len(embeddings), model=self.model)
            return embeddings

        except Exception as exc:
            logger.error("gemini_api_error", error=str(exc), model=self.model)
            raise EmbeddingProviderError(f"Gemini API error: {exc}") from exc

    async def embed_batched(self, texts: list[str], batch_size: int = 50) -> list[list[float]]:
        """Embed texts in batches."""
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch_embeddings = await self.embed(texts[i : i + batch_size])
            all_embeddings.extend(batch_embeddings)
        return all_embeddings

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def embedding_dimensions(self) -> int:
        return self.dimensions
