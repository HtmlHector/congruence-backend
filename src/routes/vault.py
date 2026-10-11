"""Vault REST API Routes for API Key & Secret Lifecycle Management."""

from __future__ import annotations

from typing import Any
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from src.contexts.congruence.models import VaultSecret, generate_id
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.auth.dependencies import get_current_user_or_none
from src.infrastructure.database.session import get_db
from src.infrastructure.security.vault import (
    encrypt_secret,
    mask_secret,
    validate_key_format,
    resolve_effective_secrets,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/vault", tags=["Secrets Vault"])


class SecretCreateRequest(BaseModel):
    provider: str = Field(..., description="AI provider: openai, anthropic, google, openrouter, or custom")
    key_name: str = Field(..., description="Key identifier: OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.")
    value: str = Field(..., description="Secret plaintext value to encrypt")
    workspace_id: str | None = Field(None, description="Optional workspace scope ID")
    description: str | None = Field(None, description="Optional secret description")


class SecretResponse(BaseModel):
    id: str
    provider: str
    key_name: str
    masked_value: str
    workspace_id: str | None
    description: str | None
    is_active: bool
    created_at: Any
    updated_at: Any


class TestKeyRequest(BaseModel):
    provider: str
    key_value: str


@router.get("/secrets", response_model=list[SecretResponse])
async def list_secrets(
    workspace_id: str | None = Query(None, description="Filter by workspace ID or user defaults"),
    db: AsyncSession = Depends(get_db),
    user: UserClaims | None = Depends(get_current_user_or_none),
) -> list[SecretResponse]:
    """List all configured secrets for the user, with optional workspace filtering."""
    if user:
        uid = user.user_id
        user_cond = (VaultSecret.user_id == uid) | (VaultSecret.user_id == "user_default") | (VaultSecret.is_active.is_(True))
    else:
        user_cond = VaultSecret.is_active.is_(True)

    query = select(VaultSecret).where(user_cond, VaultSecret.is_active.is_(True))
    if workspace_id:
        query = query.where(
            (VaultSecret.workspace_id == workspace_id)
            | (VaultSecret.workspace_id.is_(None))
            | (VaultSecret.is_active.is_(True))
        )
    
    query = query.order_by(VaultSecret.created_at.desc())
    result = await db.execute(query)
    secrets = result.scalars().all()
    
    return [
        SecretResponse(
            id=s.id,
            provider=s.provider,
            key_name=s.key_name,
            masked_value=s.masked_value,
            workspace_id=s.workspace_id,
            description=s.description,
            is_active=s.is_active,
            created_at=s.created_at.isoformat() if s.created_at else None,
            updated_at=s.updated_at.isoformat() if s.updated_at else None,
        )
        for s in secrets
    ]


@router.post("/secrets", response_model=SecretResponse, status_code=status.HTTP_201_CREATED)
async def create_or_update_secret(
    payload: SecretCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: UserClaims | None = Depends(get_current_user_or_none),
) -> SecretResponse:
    """Store or update an encrypted API key in the vault."""
    clean_val = payload.value.strip().strip('"').strip("'").strip()
    is_valid, err_msg = validate_key_format(payload.provider, clean_val)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg or "Invalid key format for provider",
        )

    uid = user.user_id if user else "user_default"
    encrypted = encrypt_secret(clean_val)
    masked = mask_secret(clean_val)

    # Check for existing secret with matching (user_id, workspace_id, key_name)
    stmt = select(VaultSecret).where(
        VaultSecret.user_id == uid,
        VaultSecret.workspace_id == payload.workspace_id,
        VaultSecret.key_name == payload.key_name,
    )
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing:
        existing.provider = payload.provider
        existing.encrypted_value = encrypted
        existing.masked_value = masked
        existing.description = payload.description
        existing.is_active = True
        secret_obj = existing
    else:
        secret_obj = VaultSecret(
            id=generate_id("sec"),
            user_id=uid,
            workspace_id=payload.workspace_id,
            provider=payload.provider,
            key_name=payload.key_name,
            encrypted_value=encrypted,
            masked_value=masked,
            description=payload.description,
            is_active=True,
        )
        db.add(secret_obj)

    await db.commit()
    await db.refresh(secret_obj)

    logger.info(
        "vault_secret_stored",
        user_id=uid,
        provider=payload.provider,
        key_name=payload.key_name,
        workspace_id=payload.workspace_id,
    )

    return SecretResponse(
        id=secret_obj.id,
        provider=secret_obj.provider,
        key_name=secret_obj.key_name,
        masked_value=secret_obj.masked_value,
        workspace_id=secret_obj.workspace_id,
        description=secret_obj.description,
        is_active=secret_obj.is_active,
        created_at=secret_obj.created_at.isoformat() if secret_obj.created_at else None,
        updated_at=secret_obj.updated_at.isoformat() if secret_obj.updated_at else None,
    )


@router.delete("/secrets/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    secret_id: str,
    db: AsyncSession = Depends(get_db),
    user: UserClaims | None = Depends(get_current_user_or_none),
) -> None:
    """Delete a secret from the vault."""
    uid = user.user_id if user else "user_default"
    stmt = delete(VaultSecret).where(
        VaultSecret.id == secret_id,
        (VaultSecret.user_id == uid) | (VaultSecret.user_id == "user_default"),
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Secret not found or access denied.",
        )
    await db.commit()


@router.post("/test-key")
async def test_key_connection(
    payload: TestKeyRequest,
    user: UserClaims | None = Depends(get_current_user_or_none),
) -> dict[str, Any]:
    """Test API key connection against official provider endpoints."""
    clean_val = payload.key_value.strip()
    provider = payload.provider.lower()

    if provider == "openai":
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {clean_val}"},
                )
                if res.status_code == 200:
                    return {"valid": True, "message": "Successfully authenticated with OpenAI API."}
                return {"valid": False, "message": f"OpenAI rejected key (HTTP {res.status_code})."}
        except Exception as e:
            return {"valid": False, "message": f"Connection error: {str(e)}"}

    elif provider == "anthropic":
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": clean_val,
                        "anthropic-version": "2023-06-01",
                    },
                )
                if res.status_code == 200:
                    return {"valid": True, "message": "Successfully authenticated with Anthropic API."}
                return {"valid": False, "message": f"Anthropic rejected key (HTTP {res.status_code})."}
        except Exception as e:
            return {"valid": False, "message": f"Connection error: {str(e)}"}

    elif provider == "openrouter":
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {clean_val}"},
                )
                if res.status_code == 200:
                    return {"valid": True, "message": "Successfully authenticated with OpenRouter API."}
                return {"valid": False, "message": f"OpenRouter rejected key (HTTP {res.status_code})."}
        except Exception as e:
            return {"valid": False, "message": f"Connection error: {str(e)}"}

    elif provider in ("commandcode", "command_code", "taste"):
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(
                    "https://api.commandcode.ai/provider/v1/models",
                    headers={"Authorization": f"Bearer {clean_val}"},
                )
                if res.status_code == 200:
                    return {"valid": True, "message": "Successfully authenticated with Command Code API."}
                elif res.status_code in (400, 402):
                    return {"valid": True, "message": "Command Code API key recognized (check credit balance)."}
                return {"valid": False, "message": f"Command Code rejected key (HTTP {res.status_code})."}
        except Exception as e:
            return {"valid": False, "message": f"Connection error: {str(e)}"}

    return {"valid": True, "message": f"Key format valid for {provider}."}
