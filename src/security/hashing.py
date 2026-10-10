"""Secure hashing and token verification utilities."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_secure_token(nbytes: int = 32) -> str:
    """Generate a cryptographically secure URL-safe random string."""
    return secrets.token_urlsafe(nbytes)


def sha256_hash(data: str) -> str:
    """Compute hex SHA-256 digest of input string."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def verify_hmac(key: bytes, message: bytes, signature: str) -> bool:
    """Constant-time comparison of HMAC-SHA256 signature."""
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
