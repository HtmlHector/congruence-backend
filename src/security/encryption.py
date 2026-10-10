"""Symmetric encryption utilities using cryptography Fernet / AES-GCM."""

from __future__ import annotations

import base64
import os

import structlog
from cryptography.fernet import Fernet

logger = structlog.get_logger(__name__)


def generate_encryption_key() -> str:
    """Generate a fresh base64-encoded 32-byte Fernet key."""
    return Fernet.generate_key().decode("utf-8")


class EncryptionService:
    """Symmetric encryption helper for secrets and tokens."""

    def __init__(self, key: str | None = None) -> None:
        raw_key = key or os.getenv("ENCRYPTION_KEY")
        if not raw_key:
            # Deterministic development fallback key
            raw_key = base64.urlsafe_b64encode(b"0" * 32).decode("utf-8")
        self._fernet = Fernet(raw_key.encode("utf-8"))

    def encrypt(self, plaintext: str) -> str:
        """Encrypt string to URL-safe ciphertext."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt URL-safe ciphertext back to plaintext."""
        return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


encryption_service = EncryptionService()
