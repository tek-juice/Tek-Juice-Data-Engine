"""
DATA ENGINE — Vector Validator
Validates embeddings and search parameters before processing.
"""

from configs.constants import (
    MIN_SIMILARITY_THRESHOLD,
    MAX_SIMILARITY_THRESHOLD,
    MAX_TOP_K,
)
from configs.settings import get_settings
from shared.exceptions.base import ValidationError

settings = get_settings()


def validate_embedding(embedding: list[float], expected_dim: int | None = None) -> None:
    """
    Validate an embedding vector.

    Args:
        embedding: List of floats representing the vector.
        expected_dim: Expected dimensionality. Uses settings default if None.

    Raises:
        ValidationError: If the embedding is malformed or wrong dimension.
    """
    if not embedding:
        raise ValidationError("Embedding vector cannot be empty.")

    dim = expected_dim or settings.embedding_dimension
    if len(embedding) != dim:
        raise ValidationError(
            f"Embedding dimension mismatch: expected {dim}, got {len(embedding)}."
        )

    if not all(isinstance(v, (int, float)) for v in embedding):
        raise ValidationError("Embedding must contain only numeric values.")

    # Check for NaN or Inf
    import math
    if any(math.isnan(v) or math.isinf(v) for v in embedding):
        raise ValidationError("Embedding contains NaN or Inf values.")


def validate_search_params(
    top_k: int,
    similarity_threshold: float,
) -> None:
    """
    Validate vector search parameters.

    Raises:
        ValidationError: If parameters are out of bounds.
    """
    if top_k < 1 or top_k > MAX_TOP_K:
        raise ValidationError(
            f"top_k must be between 1 and {MAX_TOP_K}. Got: {top_k}."
        )

    if not (MIN_SIMILARITY_THRESHOLD <= similarity_threshold <= MAX_SIMILARITY_THRESHOLD):
        raise ValidationError(
            f"similarity_threshold must be between "
            f"{MIN_SIMILARITY_THRESHOLD} and {MAX_SIMILARITY_THRESHOLD}. "
            f"Got: {similarity_threshold}."
        )


def validate_chunk_config(min_tokens: int, max_tokens: int, overlap_tokens: int) -> None:
    """
    Validate chunking configuration parameters.

    Raises:
        ValidationError: If the configuration is invalid.
    """
    from configs.constants import MIN_CHUNK_TOKENS, MAX_CHUNK_TOKENS
    from shared.exceptions.base import InvalidChunkConfigError

    if min_tokens < MIN_CHUNK_TOKENS:
        raise InvalidChunkConfigError(
            f"min_tokens must be >= {MIN_CHUNK_TOKENS}. Got: {min_tokens}."
        )
    if max_tokens > MAX_CHUNK_TOKENS:
        raise InvalidChunkConfigError(
            f"max_tokens must be <= {MAX_CHUNK_TOKENS}. Got: {max_tokens}."
        )
    if min_tokens >= max_tokens:
        raise InvalidChunkConfigError("min_tokens must be less than max_tokens.")
    if overlap_tokens >= min_tokens:
        raise InvalidChunkConfigError("overlap_tokens must be less than min_tokens.")
    if overlap_tokens < 0:
        raise InvalidChunkConfigError("overlap_tokens cannot be negative.")
