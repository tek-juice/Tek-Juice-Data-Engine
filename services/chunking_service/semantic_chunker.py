"""
DATA ENGINE — Semantic Chunker
Splits text at natural semantic boundaries by detecting cosine similarity
drops between adjacent sentence embeddings.
Phase 1 (optional upgrade): Higher quality chunks for complex documents.
"""

from dataclasses import dataclass

import numpy as np
import structlog

from configs.settings import get_settings
from shared.utils.text import split_into_sentences

logger = structlog.get_logger(__name__)
settings = get_settings()


@dataclass
class SemanticChunk:
    chunk_index: int
    text: str
    token_count: int
    char_start: int
    char_end: int
    strategy: str = "semantic"


class SemanticChunker:
    """
    Groups sentences into chunks by detecting semantic breaks.
    A new chunk starts when the cosine similarity between consecutive
    sentence embeddings drops below the configured threshold.

    Parameters:
        similarity_threshold: Cosine similarity below which a new chunk starts (default 0.85)
        min_chunk_size:        Minimum character count per chunk (default 100)
        embed_fn:              Callable that takes list[str] → list[list[float]]
    """

    def __init__(
        self,
        similarity_threshold: float | None = None,
        min_chunk_size: int = 100,
        embed_fn=None,
    ) -> None:
        self.similarity_threshold = (
            similarity_threshold or settings.semantic_chunk_similarity_threshold
        )
        self.min_chunk_size = min_chunk_size
        self._embed_fn = embed_fn  # Injected at runtime to avoid circular imports

    def chunk(self, text: str) -> list[SemanticChunk]:
        """
        Segment text into semantically coherent chunks.

        Falls back to single chunk if text is too short or embedding unavailable.
        """
        if not text or not text.strip():
            return []

        sentences = split_into_sentences(text)
        if len(sentences) <= 1:
            return self._single_chunk(text)

        if self._embed_fn is None:
            logger.warning("semantic_chunker_no_embed_fn_falling_back")
            return self._single_chunk(text)

        embeddings = self._embed_fn(sentences)
        if not embeddings or len(embeddings) != len(sentences):
            return self._single_chunk(text)

        # Find break points where similarity drops below threshold
        break_indices = self._find_break_points(embeddings)

        chunks = self._build_chunks(sentences, break_indices)
        logger.debug(
            "semantic_chunking_complete",
            sentences=len(sentences),
            chunks_produced=len(chunks),
            threshold=self.similarity_threshold,
        )
        return chunks

    def _find_break_points(self, embeddings: list[list[float]]) -> list[int]:
        """Return indices where a new chunk should start."""
        breaks = [0]
        vecs = [np.array(e, dtype=np.float32) for e in embeddings]

        for i in range(1, len(vecs)):
            sim = self._cosine_similarity(vecs[i - 1], vecs[i])
            if sim < self.similarity_threshold:
                breaks.append(i)

        return breaks

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _build_chunks(
        self, sentences: list[str], break_indices: list[int]
    ) -> list[SemanticChunk]:
        chunks: list[SemanticChunk] = []
        break_indices_set = set(break_indices)
        current_sentences: list[str] = []
        char_cursor = 0
        chunk_index = 0

        for i, sentence in enumerate(sentences):
            if i in break_indices_set and current_sentences:
                chunk_text = " ".join(current_sentences)
                if len(chunk_text) >= self.min_chunk_size:
                    chunks.append(
                        SemanticChunk(
                            chunk_index=chunk_index,
                            text=chunk_text,
                            token_count=len(chunk_text.split()),
                            char_start=char_cursor - len(chunk_text),
                            char_end=char_cursor,
                        )
                    )
                    chunk_index += 1
                current_sentences = []

            current_sentences.append(sentence)
            char_cursor += len(sentence) + 1

        # Flush remaining sentences
        if current_sentences:
            chunk_text = " ".join(current_sentences)
            chunks.append(
                SemanticChunk(
                    chunk_index=chunk_index,
                    text=chunk_text,
                    token_count=len(chunk_text.split()),
                    char_start=char_cursor - len(chunk_text),
                    char_end=char_cursor,
                )
            )

        return chunks

    def _single_chunk(self, text: str) -> list[SemanticChunk]:
        return [
            SemanticChunk(
                chunk_index=0,
                text=text,
                token_count=len(text.split()),
                char_start=0,
                char_end=len(text),
            )
        ]
