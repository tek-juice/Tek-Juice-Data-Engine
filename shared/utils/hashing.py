"""
DATA ENGINE — Hashing & Fingerprinting Utilities
Used for deduplication, cache keys, and API key storage.
"""

import hashlib
import hmac
import uuid
from typing import Any


def sha256_hex(data: str | bytes) -> str:
    """Return SHA-256 hex digest of the input."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def md5_hex(data: str | bytes) -> str:
    """Return MD5 hex digest (for non-security use, e.g. cache keys)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.md5(data).hexdigest()  # noqa: S324


def hmac_sha256(key: str, message: str) -> str:
    """Generate HMAC-SHA256 signature."""
    return hmac.new(
        key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def content_fingerprint(text: str) -> str:
    """
    Generate a stable fingerprint for a text chunk.
    Used for deduplication before storing embeddings.
    Normalises whitespace before hashing to catch near-duplicates.
    """
    import re
    normalised = re.sub(r"\s+", " ", text.strip().lower())
    return sha256_hex(normalised)


def document_fingerprint(filename: str, content: bytes) -> str:
    """Generate a fingerprint combining filename and content hash."""
    name_hash = sha256_hex(filename)
    content_hash = sha256_hex(content)
    combined = f"{name_hash}:{content_hash}"
    return sha256_hex(combined)


def cache_key(*parts: Any) -> str:
    """
    Build a Redis cache key from parts.
    Example: cache_key("embeddings", tenant_id, document_id)
    → "embeddings:abc123:def456"
    """
    return ":".join(str(p) for p in parts)


def hash_api_key(api_key: str) -> str:
    """
    Hash an API key for secure storage.
    Use SHA-256 — fast enough for lookup, secure enough for keys.
    Never store plaintext API keys.
    """
    return sha256_hex(api_key)


def generate_request_id() -> str:
    """Generate a unique request trace ID."""
    return str(uuid.uuid4())
