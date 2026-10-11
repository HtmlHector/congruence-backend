"""Vault Security Engine - AES-256 / Fernet Encryption and Secret Lifecycle."""

from __future__ import annotations

import base64
import hashlib
import os
import re
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from src.config.settings import settings
from src.contexts.congruence.models import VaultSecret

logger = structlog.get_logger(__name__)


def _get_fernet_key() -> bytes:
    """Derive a 32-byte URL-safe base64 encryption key from server secret."""
    raw_key = (
        os.getenv("VAULT_ENCRYPTION_KEY")
        or getattr(settings.auth, "supabase_jwt_secret", None)
        or "congruence-default-vault-secure-key-2026"
    )
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt plaintext string using Fernet (AES-128-CBC + HMAC-SHA256)."""
    if not plaintext:
        return ""
    f = Fernet(_get_fernet_key())
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt ciphertext back to plaintext string."""
    if not ciphertext:
        return ""
    try:
        f = Fernet(_get_fernet_key())
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        logger.error("vault_decryption_failed", error=str(exc))
        return ""


def mask_secret(plaintext: str) -> str:
    """Produce safe masked preview string, e.g. sk-ant-••••••••58f2."""
    if not plaintext:
        return "••••••••"
    if len(plaintext) <= 8:
        return "••••" + plaintext[-2:]

    if plaintext.startswith("sk-proj-"):
        prefix = "sk-proj-"
    elif plaintext.startswith("sk-ant-"):
        prefix = "sk-ant-"
    elif plaintext.startswith("AIzaSy"):
        prefix = "AIzaSy"
    elif plaintext.startswith("sk-or-"):
        prefix = "sk-or-"
    elif plaintext.startswith("sk-"):
        prefix = "sk-"
    else:
        prefix = plaintext[:4]

    suffix = plaintext[-4:]
    return f"{prefix}••••••••{suffix}"


def validate_key_format(provider: str, key_value: str) -> tuple[bool, str | None]:
    """Validate provider-specific API key format."""
    val = key_value.strip().strip('"').strip("'").strip()
    if not val:
        return False, "API key cannot be empty."
    if len(val) < 8:
        return False, "API key is too short."
    
    if provider == "openai":
        if not (val.startswith("sk-") or val.startswith("sk-proj-")) and len(val) < 24:
            return False, "OpenAI API keys typically begin with 'sk-' or 'sk-proj-'."
    elif provider == "anthropic":
        if not val.startswith("sk-ant-") and len(val) < 24:
            return False, "Anthropic API keys typically begin with 'sk-ant-'."
    elif provider == "google":
        if not val.startswith("AIza") and len(val) < 24:
            return False, "Google Gemini API keys typically begin with 'AIza'."
    elif provider == "openrouter":
        if not val.startswith("sk-or-") and len(val) < 24:
            return False, "OpenRouter API keys typically begin with 'sk-or-'."
            
    return True, None


async def resolve_effective_secrets(
    db: AsyncSession,
    user_id: str,
    workspace_id: str | None = None,
) -> dict[str, str]:
    """Resolve active decrypted secrets for a container or PTY session.
    
    Resolution hierarchy:
    1. User-level keys and active provider secrets
    2. Workspace-level overrides (workspace_id == target_workspace_id)
    """
    effective: dict[str, str] = {}
    
    # 1. Fetch user-level default keys (including user_default, user_id, or active keys)
    stmt_user = select(VaultSecret).where(
        (VaultSecret.user_id == user_id) | (VaultSecret.user_id == "user_default") | (VaultSecret.is_active.is_(True)),
        VaultSecret.is_active.is_(True),
    )
    res_user = await db.execute(stmt_user)
    for s in res_user.scalars().all():
        decrypted = decrypt_secret(s.encrypted_value)
        if decrypted:
            effective[s.key_name] = decrypted
            
    # 2. Fetch workspace-level overrides if workspace_id provided
    if workspace_id:
        stmt_ws = select(VaultSecret).where(
            VaultSecret.workspace_id == workspace_id,
            VaultSecret.is_active.is_(True),
        )
        res_ws = await db.execute(stmt_ws)
        for s in res_ws.scalars().all():
            decrypted = decrypt_secret(s.encrypted_value)
            if decrypted:
                effective[s.key_name] = decrypted
                
    return effective
