"""
DATA ENGINE — Unit Tests: Security
Tests for password hashing, JWT token creation/validation,
API key generation, and security utility functions.
Run with: pytest tests/unit/test_security.py -v
"""

import os
import pytest
from jose import JWTError

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-that-is-at-least-32-chars-long!")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-that-is-at-least-32-chars!")
os.environ.setdefault("POSTGRES_PASSWORD", "test-password")

from configs.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token, is_token_valid,
    generate_api_key, generate_secret_key,
    build_rls_policy_sql, set_tenant_context_sql, SECURITY_HEADERS,
)


class TestPasswordHashing:

    def test_hash_is_not_plaintext(self):
        pw = "super-secret-password"
        hashed = hash_password(pw)
        assert hashed != pw

    def test_verify_correct_password(self):
        pw = "correct-horse-battery"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("rightpassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_same_password_different_hashes(self):
        pw = "test-password"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        assert h1 != h2  # bcrypt uses random salt

    def test_empty_password_hashable(self):
        # Should not raise — empty passwords are technically valid
        hashed = hash_password("")
        assert len(hashed) > 0


class TestJWT:

    SUBJECT  = "user-uuid-1234"
    TENANT   = "tenant-uuid-5678"

    def test_create_and_decode_access_token(self):
        token = create_access_token(self.SUBJECT, self.TENANT)
        payload = decode_token(token)
        assert payload["sub"] == self.SUBJECT
        assert payload["tenant_id"] == self.TENANT
        assert payload["type"] == "access"

    def test_refresh_token_type(self):
        token = create_refresh_token(self.SUBJECT, self.TENANT)
        payload = decode_token(token)
        assert payload["type"] == "refresh"

    def test_extra_claims_embedded(self):
        token = create_access_token(self.SUBJECT, self.TENANT, extra_claims={"role": "admin"})
        payload = decode_token(token)
        assert payload["role"] == "admin"

    def test_is_valid_for_fresh_token(self):
        token = create_access_token(self.SUBJECT, self.TENANT)
        assert is_token_valid(token) is True

    def test_is_invalid_for_garbage(self):
        assert is_token_valid("not.a.jwt") is False

    def test_decode_invalid_token_raises(self):
        with pytest.raises(JWTError):
            decode_token("totally.invalid.token")

    def test_tampered_token_fails(self):
        token = create_access_token(self.SUBJECT, self.TENANT)
        # Corrupt the payload portion
        parts = token.split(".")
        parts[1] = parts[1][:-3] + "xyz"
        tampered = ".".join(parts)
        assert is_token_valid(tampered) is False


class TestAPIKeyGeneration:

    def test_api_key_has_correct_prefix(self):
        key = generate_api_key()
        assert key.startswith("de_")

    def test_api_key_length(self):
        key = generate_api_key()
        assert len(key) > 40

    def test_api_keys_are_unique(self):
        keys = {generate_api_key() for _ in range(100)}
        assert len(keys) == 100

    def test_secret_key_is_hex(self):
        key = generate_secret_key(64)
        assert all(c in "0123456789abcdef" for c in key)
        assert len(key) == 64

    def test_secret_key_length_configurable(self):
        assert len(generate_secret_key(32)) == 32
        assert len(generate_secret_key(128)) == 128


class TestRLSHelpers:

    def test_rls_policy_sql_contains_table(self):
        sql = build_rls_policy_sql("documents")
        assert "documents" in sql
        assert "ROW LEVEL SECURITY" in sql
        assert "tenant_isolation" in sql

    def test_tenant_context_sql_contains_id(self):
        tid = "abc-123"
        sql = set_tenant_context_sql(tid)
        assert tid in sql
        assert "app.current_tenant_id" in sql


class TestSecurityHeaders:

    def test_required_headers_present(self):
        required = [
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Strict-Transport-Security",
            "X-XSS-Protection",
            "Referrer-Policy",
        ]
        for header in required:
            assert header in SECURITY_HEADERS

    def test_x_frame_is_deny(self):
        assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"

    def test_hsts_includes_subdomain(self):
        assert "includeSubDomains" in SECURITY_HEADERS["Strict-Transport-Security"]
