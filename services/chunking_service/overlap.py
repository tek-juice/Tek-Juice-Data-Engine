"""
DATA ENGINE — Chunk Overlap Utilities
Handles overlap token injection and boundary merging between chunks.
"""

import tiktoken
from configs.constants import DEFAULT_TOKENIZER_ENCODING


def inject_overlap(
    chunks: list[str],
    overlap_tokens: int,
    encoding_name: str = DEFAULT_TOKENIZER_ENCODING,
) -> list[str]:
    """
    Post-process a list of chunk texts by prepending the last N tokens
    of the previous chunk to each subsequent chunk.

    This is a secondary pass — use when chunks have been produced
    without built-in overlap (e.g. from an external splitter).

    Args:
        chunks:         List of plain text chunks.
        overlap_tokens: Number of tokens to carry over between chunks.
        encoding_name:  Tiktoken encoding.

    Returns:
        List of chunks with overlap prepended.
    """
    if not chunks or overlap_tokens <= 0:
        return chunks

    enc = tiktoken.get_encoding(encoding_name)
    result: list[str] = [chunks[0]]

    for i in range(1, len(chunks)):
        prev_tokens = enc.encode(chunks[i - 1])
        overlap_tail = prev_tokens[-overlap_tokens:] if len(prev_tokens) >= overlap_tokens else prev_tokens
        overlap_text = enc.decode(overlap_tail)
        result.append(overlap_text + " " + chunks[i])

    return result


def remove_overlap_from_chunk(
    chunk_text: str,
    overlap_tokens: int,
    encoding_name: str = DEFAULT_TOKENIZER_ENCODING,
) -> str:
    """
    Strip the leading overlap tokens from a chunk (for deduplication/display).

    Args:
        chunk_text:     Chunk text with prepended overlap.
        overlap_tokens: Number of leading overlap tokens to strip.

    Returns:
        Chunk text with overlap removed.
    """
    enc = tiktoken.get_encoding(encoding_name)
    tokens = enc.encode(chunk_text)
    if len(tokens) <= overlap_tokens:
        return chunk_text
    return enc.decode(tokens[overlap_tokens:])


def merge_short_chunks(
    chunks: list[str],
    min_tokens: int,
    encoding_name: str = DEFAULT_TOKENIZER_ENCODING,
) -> list[str]:
    """
    Merge chunks that are below min_tokens into the preceding chunk.
    Prevents very small final chunks from being sent to the embedding API.

    Args:
        chunks:     List of chunk texts.
        min_tokens: Minimum acceptable token count per chunk.

    Returns:
        Merged chunk list.
    """
    if not chunks:
        return chunks

    enc = tiktoken.get_encoding(encoding_name)
    merged: list[str] = []

    for chunk in chunks:
        token_count = len(enc.encode(chunk))
        if merged and token_count < min_tokens:
            merged[-1] = merged[-1] + " " + chunk
        else:
            merged.append(chunk)

    return merged
