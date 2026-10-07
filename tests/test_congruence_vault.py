"""Tests for AES-256-GCM Credential Vault."""

import pytest
from cryptography.exceptions import InvalidTag

from src.infrastructure.security.vault import VaultService


def test_vault_encryption_and_decryption():
    key = b"12345678901234567890123456789012"  # 32 bytes
    vault = VaultService(master_key=key)

    plaintext = "sk-ant-api03-congruence-test-token-123456789"
    encrypted = vault.encrypt(plaintext)

    assert encrypted != plaintext
    assert isinstance(encrypted, str)

    decrypted = vault.decrypt(encrypted)
    assert decrypted == plaintext


def test_vault_ciphertext_randomization():
    key = b"12345678901234567890123456789012"
    vault = VaultService(master_key=key)

    plaintext = "github_pat_11AAAAAAA11111111"
    enc1 = vault.encrypt(plaintext)
    enc2 = vault.encrypt(plaintext)

    # Different 96-bit nonces mean ciphertexts are distinct
    assert enc1 != enc2
    assert vault.decrypt(enc1) == plaintext
    assert vault.decrypt(enc2) == plaintext


def test_vault_tamper_rejection():
    key = b"12345678901234567890123456789012"
    vault = VaultService(master_key=key)

    plaintext = "sk-proj-openai-test-token"
    encrypted = vault.encrypt(plaintext)

    # Tamper with ciphertext string
    tampered = encrypted[:-2] + ("AA" if encrypted[-2:] != "AA" else "BB")
    with pytest.raises((InvalidTag, Exception)):
        vault.decrypt(tampered)
