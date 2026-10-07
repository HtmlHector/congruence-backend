"""AES-256-GCM Encrypted Credential Vault for BYO-API Keys."""

from __future__ import annotations

import base64
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config.settings import get_settings


class VaultService:
    """Provides authenticated AES-256-GCM encryption for stored agent credentials."""

    def __init__(self, master_key: bytes | None = None) -> None:
        if master_key is not None:
            self._key = master_key
        else:
            settings = get_settings()
            secret_str = getattr(settings, "VAULT_SECRET_KEY", None) or "0" * 64
            # Derive 32-byte key
            if len(secret_str) == 64:
                try:
                    self._key = bytes.fromhex(secret_str)
                except ValueError:
                    self._key = secret_str.encode("utf-8")[:32].ljust(32, b"\0")
            else:
                self._key = secret_str.encode("utf-8")[:32].ljust(32, b"\0")

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string into base64 nonce+ciphertext+tag."""
        aesgcm = AESGCM(self._key)
        nonce = secrets.token_bytes(12)  # 96-bit nonce for AES-GCM
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        combined = nonce + ciphertext
        return base64.b64encode(combined).decode("ascii")

    def decrypt(self, encrypted_b64: str) -> str:
        """Decrypt base64 nonce+ciphertext+tag back into plaintext string."""
        raw = base64.b64decode(encrypted_b64.encode("ascii"))
        nonce = raw[:12]
        ciphertext = raw[12:]
        aesgcm = AESGCM(self._key)
        decrypted = aesgcm.decrypt(nonce, ciphertext, None)
        return decrypted.decode("utf-8")


_vault_instance: VaultService | None = None


def get_vault_service() -> VaultService:
    """Return singleton VaultService instance."""
    global _vault_instance
    if _vault_instance is None:
        _vault_instance = VaultService()
    return _vault_instance
