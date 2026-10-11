"""Tests for Vault API Key & Secret Management System."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.security.vault import (
    encrypt_secret,
    decrypt_secret,
    mask_secret,
    validate_key_format,
    resolve_effective_secrets,
)
from src.contexts.congruence.models import VaultSecret


def test_vault_encryption_and_decryption():
    raw_key = "sk-ant-api03-test-anthropic-secret-key-123456"
    encrypted = encrypt_secret(raw_key)
    assert encrypted != raw_key
    assert len(encrypted) > 20
    
    decrypted = decrypt_secret(encrypted)
    assert decrypted == raw_key


def test_vault_masking():
    assert mask_secret("sk-ant-api03-abcdef1234") == "sk-ant-••••••••1234"
    assert mask_secret("AIzaSyTestGoogleKey9876") == "AIzaSy••••••••9876"
    assert mask_secret("short") == "••••rt"


def test_vault_key_format_validation():
    valid, _ = validate_key_format("openai", "sk-proj-1234567890abcdef")
    assert valid is True
    
    valid, err = validate_key_format("openai", "invalid-key")
    assert valid is False
    assert err is not None

    valid, _ = validate_key_format("anthropic", "sk-ant-123456")
    assert valid is True
    
    valid, _ = validate_key_format("google", "AIzaSy123456")
    assert valid is True


@pytest.mark.asyncio
async def test_vault_crud_endpoints(client: AsyncClient):
    # 1. Create a secret
    payload = {
        "provider": "openai",
        "key_name": "OPENAI_API_KEY",
        "value": "sk-proj-test-openai-key-9999",
        "workspace_id": None,
        "description": "User default OpenAI key",
    }
    
    headers = {"Authorization": "Bearer mock_user_vault_test"}
    res = await client.post("/api/v1/vault/secrets", json=payload, headers=headers)
    assert res.status_code == 201
    data = res.json()
    assert data["provider"] == "openai"
    assert data["key_name"] == "OPENAI_API_KEY"
    assert "sk-proj-••••••••9999" in data["masked_value"]
    secret_id = data["id"]
    
    # 2. List secrets
    res_list = await client.get("/api/v1/vault/secrets", headers=headers)
    assert res_list.status_code == 200
    secrets = res_list.json()
    assert len(secrets) >= 1
    assert any(s["id"] == secret_id for s in secrets)
    
    # 3. Delete secret
    res_del = await client.delete(f"/api/v1/vault/secrets/{secret_id}", headers=headers)
    assert res_del.status_code == 204
