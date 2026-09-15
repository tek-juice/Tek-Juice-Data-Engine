"""
DATA ENGINE — Credential Encryption
AES-256-GCM encryption for storing injection credentials at rest.
Credentials are never stored in plaintext — only the encrypted blob
is written to the database.
"""

import base64
import json
import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _get_key() -> bytes:
    """
    Load the 32-byte AES key from settings.
    Falls back to a random ephemeral key in development (credentials
    will not survive a restart — acceptable for dev only).
    """
    from configs.settings import get_settings
    s = get_settings()
    hex_key = s.credentials_encryption_key
    if hex_key and len(hex_key) == 64:
        return bytes.fromhex(hex_key)
    # Dev fallback — log a loud warning
    logger.warning(
        "credentials_encryption_key_not_set",
        advice="Set CREDENTIALS_ENCRYPTION_KEY in .env (64 hex chars) before production use.",
    )
    return os.urandom(32)   # ephemeral — dev only


def encrypt_credentials(creds: dict[str, Any]) -> str:
    """
    Encrypt a credentials dict to a base64 string using AES-256-GCM.
    Returns a single string safe for database storage.
    Format: base64(nonce + tag + ciphertext)
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        # Fallback: store as base64-encoded JSON (development only)
        logger.warning("cryptography_not_installed_storing_as_b64_dev_only")
        raw = json.dumps(creds).encode()
        return "b64:" + base64.b64encode(raw).decode()

    key    = _get_key()
    nonce  = os.urandom(12)   # 96-bit nonce for GCM
    aesgcm = AESGCM(key)
    ct     = aesgcm.encrypt(nonce, json.dumps(creds).encode(), None)
    # ct already includes the 16-byte GCM tag appended by the library
    blob   = base64.b64encode(nonce + ct).decode()
    return "gcm:" + blob


def decrypt_credentials(blob: str) -> dict[str, Any]:
    """
    Decrypt a blob produced by encrypt_credentials().
    Raises ValueError on authentication failure (tampered data).
    """
    if blob.startswith("b64:"):
        raw = base64.b64decode(blob[4:])
        return json.loads(raw)

    if not blob.startswith("gcm:"):
        raise ValueError("Unknown credential blob format.")

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError("cryptography package required. pip install cryptography")

    key    = _get_key()
    data   = base64.b64decode(blob[4:])
    nonce  = data[:12]
    ct     = data[12:]
    aesgcm = AESGCM(key)
    plain  = aesgcm.decrypt(nonce, ct, None)
    return json.loads(plain)
