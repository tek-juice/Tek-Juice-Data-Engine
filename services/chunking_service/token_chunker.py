"""
DATA ENGINE — Token Chunker
Splits text into fixed-size token windows with configurable overlap.
Uses tiktoken (cl100k_base) for accurate token counting.
Phase 1: Core chunking strategy for the embedding pipeline.
"""

from dataclasses import dataclass
from typing import Iterator

import tiktoken
import structlog

from configs.constants import DEFAULT_TOKENIZER_ENCODING
from configs.settings import get_settings
from shared.validators.vector_validator import validate_chunk_config

logger = structlog.get_logger(__name__)
settings = get_settings()


@dataclass
class Chunk:
    """Represents a single text chunk with positional metadata."""
    chunk_index: int
    text: str
    token_count: int
    char_start: int
    char_end: int
    strategy: str = "token"


class TokenChunker:
    """
    Splits text into overlapping token windows.

    Parameters:
        min_tokens:     Minimum tokens per chunk (default 256)
        max_tokens:     Maximum tokens per chunk (default 512)
        overlap_tokens: Token overlap between adjacent chunks (default 64)
        encoding_name:  Tiktoken encoding name (default cl100k_base)
    """

    def __init__(
        self,
        min_tokens: int | None = None,
        max_tokens: int | None = None,
        overlap_tokens: int | None = None,
        encoding_name: str = DEFAULT_TOKENIZER_ENCODING,
    ) -> None:
        self.min_tokens = min_tokens or settings.chunk_min_tokens
        self.max_tokens = max_tokens or settings.chunk_max_tokens
        self.overlap_tokens = overlap_tokens or settings.chunk_overlap_tokens
        self.encoding = tiktoken.get_encoding(encoding_name)

        validate_chunk_config(self.min_tokens, self.max_tokens, self.overlap_tokens)

    def chunk(self, text: str) -> list[Chunk]:
        """
        Split text into overlapping token-window chunks.

        Args:
            text: Input plain text to chunk.

        Returns:
            List of Chunk objects with token counts and char positions.
        """
        if not text or not text.strip():
            return []

        tokens = self.encoding.encode(text)
        total_tokens = len(tokens)

        if total_tokens == 0:
            return []

        chunks: list[Chunk] = []
        chunk_index = 0
        start = 0
        step = self.max_tokens - self.overlap_tokens

        while start < total_tokens:
            end = min(start + self.max_tokens, total_tokens)
            chunk_tokens = tokens[start:end]
            token_count = len(chunk_tokens)

            # Skip chunks that are too small (except the final chunk)
            if token_count < self.min_tokens and start > 0 and end < total_tokens:
                break

            chunk_text = self.encoding.decode(chunk_tokens)
            char_start = len(self.encoding.decode(tokens[:start]))
            char_end = char_start + len(chunk_text)

            chunks.append(
                Chunk(
                    chunk_index=chunk_index,
                    text=chunk_text,
                    token_count=token_count,
                    char_start=char_start,
                    char_end=char_end,
                )
            )

            chunk_index += 1
            start += step

            if end >= total_tokens:
                break

        logger.debug(
            "token_chunking_complete",
            total_tokens=total_tokens,
            chunks_produced=len(chunks),
            max_tokens=self.max_tokens,
            overlap=self.overlap_tokens,
        )
        return chunks

    def count_tokens(self, text: str) -> int:
        """Return the token count of a text string."""
        return len(self.encoding.encode(text))

    def estimate_chunks(self, text: str) -> int:
        """Estimate how many chunks the text will produce."""
        total_tokens = self.count_tokens(text)
        if total_tokens <= self.max_tokens:
            return 1
        step = self.max_tokens - self.overlap_tokens
        return max(1, (total_tokens + step - 1) // step)
