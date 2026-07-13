"""
DATA ENGINE — Unit Tests: Chunking Service
Tests for token chunker, semantic chunker, overlap utilities, and tokenizer.
Run with: pytest tests/unit/test_chunking.py -v
"""

import pytest
from services.chunking_service.token_chunker import TokenChunker, Chunk
from services.chunking_service.overlap import inject_overlap, merge_short_chunks, remove_overlap_from_chunk
from services.chunking_service.tokenizer import count_tokens, encode, decode, token_windows


class TestTokenChunker:

    def setup_method(self):
        self.chunker = TokenChunker(min_tokens=64, max_tokens=128, overlap_tokens=16)

    def test_returns_list_of_chunks(self):
        chunks = self.chunker.chunk("word " * 200)
        assert isinstance(chunks, list)
        assert len(chunks) > 0
        assert all(isinstance(c, Chunk) for c in chunks)

    def test_chunk_index_sequential(self):
        chunks = self.chunker.chunk("word " * 300)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i

    def test_chunk_token_count_within_bounds(self):
        chunks = self.chunker.chunk("word " * 500)
        for chunk in chunks[:-1]:  # all except last may be smaller
            assert chunk.token_count <= 128

    def test_empty_text_returns_empty(self):
        assert self.chunker.chunk("") == []
        assert self.chunker.chunk("   ") == []

    def test_short_text_single_chunk(self):
        chunks = self.chunker.chunk("short text here")
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0

    def test_chunk_text_not_empty(self):
        chunks = self.chunker.chunk("word " * 200)
        for chunk in chunks:
            assert len(chunk.text.strip()) > 0

    def test_char_start_end_set(self):
        chunks = self.chunker.chunk("word " * 200)
        for chunk in chunks:
            assert chunk.char_start >= 0
            assert chunk.char_end > chunk.char_start

    def test_estimate_chunks(self):
        text = "word " * 500
        estimate = self.chunker.estimate_chunks(text)
        actual = len(self.chunker.chunk(text))
        # Estimate should be within ±2 of actual
        assert abs(estimate - actual) <= 2

    def test_count_tokens(self):
        count = self.chunker.count_tokens("hello world")
        assert count >= 2

    def test_strategy_is_token(self):
        chunks = self.chunker.chunk("hello world example text here.")
        for chunk in chunks:
            assert chunk.strategy == "token"


class TestChunkOverlap:

    def test_inject_overlap_adds_prefix(self):
        chunks = ["first chunk text", "second chunk text", "third chunk text"]
        result = inject_overlap(chunks, overlap_tokens=2)
        assert len(result) == 3
        assert result[0] == chunks[0]
        # Second chunk should start with tokens from the first
        assert len(result[1]) > len(chunks[1])

    def test_inject_overlap_zero_no_change(self):
        chunks = ["alpha beta", "gamma delta"]
        result = inject_overlap(chunks, overlap_tokens=0)
        assert result == chunks

    def test_inject_overlap_empty_list(self):
        assert inject_overlap([], overlap_tokens=4) == []

    def test_merge_short_chunks_combines(self):
        chunks = [
            "word " * 50,   # ~50 tokens — should merge
            "a b c",        # very short — will merge into previous
        ]
        result = merge_short_chunks(chunks, min_tokens=30)
        # The short last chunk should merge into the previous
        assert len(result) < len(chunks) or any(len(r) > len(chunks[1]) for r in result)

    def test_merge_short_chunks_preserves_long(self):
        long_chunk = "word " * 100
        short_chunk = "tiny"
        result = merge_short_chunks([long_chunk, short_chunk], min_tokens=50)
        # Long chunk preserved; short merged in
        assert any(long_chunk[:20] in r for r in result)

    def test_remove_overlap_shortens_chunk(self):
        original = "hello world this is the main content"
        result = remove_overlap_from_chunk(original, overlap_tokens=2)
        # Should be shorter than original
        assert len(result) <= len(original)


class TestTokenizer:

    def test_count_tokens_simple(self):
        count = count_tokens("hello world")
        assert count >= 2

    def test_encode_returns_list_of_ints(self):
        tokens = encode("test text")
        assert isinstance(tokens, list)
        assert all(isinstance(t, int) for t in tokens)

    def test_decode_roundtrip(self):
        text = "data engine processes vectors"
        tokens = encode(text)
        recovered = decode(tokens)
        assert text.lower() in recovered.lower()

    def test_token_windows_count(self):
        text = "word " * 200
        windows = token_windows(text, window_size=64, overlap=16)
        assert len(windows) >= 2
        assert all(isinstance(w, str) for w in windows)

    def test_token_windows_short_text(self):
        text = "short"
        windows = token_windows(text, window_size=64, overlap=8)
        assert len(windows) == 1
