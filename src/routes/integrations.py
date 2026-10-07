"""REST API routes for GitHub App, Claude OAuth, and Agent Integrations."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.congruence.models import Project, VaultSecret
from src.infrastructure.database.session import get_db_session
from src.infrastructure.security.vault import get_vault_service

router = APIRouter(prefix="/integrations", tags=["Integrations & OAuth"])


class StoreKeyPayload(BaseModel):
    provider: str = Field(..., pattern="^(openai|anthropic|github|openrouter)$")
    key_name: str
    plaintext_value: str = Field(..., min_length=1)


@router.get("/status/{project_id}")
async def get_integrations_status(
    project_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Return status of connected integrations for the project."""
    res = await db.execute(select(VaultSecret).where(VaultSecret.project_id == project_id))
    secrets = res.scalars().all()
    secret_keys = {s.key_name for s in secrets}

    github_app_id = os.getenv("GITHUB__APP_ID") or os.getenv("GITHUB_APP_ID", "4010628")

    return {
        "project_id": project_id,
        "github": {
            "connected": True,
            "app_id": github_app_id,
            "install_url": "https://github.com/apps/congruence-app/installations/new",
            "repo": "parabox/sample-app",
        },
        "claude_code": {
            "auth_type": "oauth_cli",
            "status": "configured" if "ANTHROPIC_API_KEY" in secret_keys else "oauth_ready",
            "login_command": "claude",
        },
        "openai_codex": {
            "auth_type": "api_key",
            "status": "connected" if "OPENAI_API_KEY" in secret_keys else "disconnected",
            "key_name": "OPENAI_API_KEY",
        },
        "openrouter": {
            "status": "connected" if "OPENROUTER_API_KEY" in secret_keys else "disconnected",
        },
    }


@router.get("/github/install-url")
async def get_github_install_url() -> dict[str, str]:
    """Return public GitHub App installation URL for repository authorization."""
    app_slug = os.getenv("GITHUB__APP_SLUG") or os.getenv("GITHUB_APP_SLUG", "congruence-dev-app")
    return {
        "install_url": f"https://github.com/apps/{app_slug}/installations/new",
        "app_slug": app_slug,
    }


@router.post("/vault/keys/{project_id}")
async def store_provider_key(
    project_id: str,
    payload: StoreKeyPayload,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Store encrypted BYO provider key in the project vault."""
    # Verify project exists
    res = await db.execute(select(Project).where(Project.id == project_id))
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    vault = get_vault_service()
    encrypted = vault.encrypt(payload.plaintext_value)

    # Check if secret already exists
    existing = await db.execute(
        select(VaultSecret).where(
            VaultSecret.project_id == project_id,
            VaultSecret.key_name == payload.key_name,
        )
    )
    secret = existing.scalar_one_or_none()
    if secret:
        secret.encrypted_value = encrypted
    else:
        secret = VaultSecret(
            project_id=project_id,
            key_name=payload.key_name,
            encrypted_value=encrypted,
            injection_path=f"$HOME/.{payload.provider}/config",
        )
        db.add(secret)

    await db.commit()
    await db.refresh(secret)

    return {
        "id": secret.id,
        "provider": payload.provider,
        "key_name": secret.key_name,
        "status": "encrypted_and_vaulted",
    }

@router.get("/vault/keys/{project_id}")
async def get_vault_keys(project_id: str, db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    res = await db.execute(select(VaultSecret).where(VaultSecret.project_id == project_id))
    secrets = res.scalars().all()
    secret_keys = {s.key_name for s in secrets}
    return {
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY" in secret_keys,
        "OPENAI_API_KEY": "OPENAI_API_KEY" in secret_keys,
        "GITHUB_TOKEN": "GITHUB_TOKEN" in secret_keys,
    }
