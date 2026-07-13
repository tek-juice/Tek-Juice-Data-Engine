"""
DATA ENGINE — Tokenizer Utilities
Thin wrapper around tiktoken for token counting and encoding operations.
Provides a shared tokenizer instance for use across the chunking service.
"""

from functools import lru_cache

import tiktoken

from configs.constants import DEFAULT_TOKENIZER_ENCODING


@lru_cache(maxsize=8)
def get_tokenizer(encoding_name: str = DEFAULT_TOKENIZER_ENCODING) -> tiktoken.Encoding:
    """Return a cached tiktoken encoding instance."""
    return tiktoken.get_encoding(encoding_name)


def count_tokens(text: str, encoding_name: str = DEFAULT_TOKENIZER_ENCODING) -> int:
    """Count the number of tokens in a text string."""
    enc = get_tokenizer(encoding_name)
    return len(enc.encode(text))


def encode(text: str, encoding_name: str = DEFAULT_TOKENIZER_ENCODING) -> list[int]:
    """Encode text to a list of token IDs."""
    return get_tokenizer(encoding_name).encode(text)


def decode(tokens: list[int], encoding_name: str = DEFAULT_TOKENIZER_ENCODING) -> str:
    """Decode a list of token IDs back to text."""
    return get_tokenizer(encoding_name).decode(tokens)


def truncate_to_tokens(
    text: str,
    max_tokens: int,
    encoding_name: str = DEFAULT_TOKENIZER_ENCODING,
) -> str:
    """Truncate text to at most max_tokens tokens."""
    enc = get_tokenizer(encoding_name)
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return enc.decode(tokens[:max_tokens])


def token_windows(
    text: str,
    window_size: int,
    overlap: int,
    encoding_name: str = DEFAULT_TOKENIZER_ENCODING,
) -> list[str]:
    """
    Generate sliding token windows over text.
    Returns decoded text strings for each window.
    """
    enc = get_tokenizer(encoding_name)
    tokens = enc.encode(text)
    step = window_size - overlap
    windows = []

    for start in range(0, len(tokens), step):
        end = min(start + window_size, len(tokens))
        windows.append(enc.decode(tokens[start:end]))
        if end >= len(tokens):
            break

    return windows
