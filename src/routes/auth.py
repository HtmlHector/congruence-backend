"""Authentication and API Key management endpoints."""

from __future__ import annotations

import secrets
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.infrastructure.auth.dependencies import (
    get_current_user,
    hash_api_key,
    require_permission,
)
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.database.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class CreateApiKeyRequest(BaseModel):
    workspace_id: str
    name: str = Field(..., min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["*"])


@router.get("/me")
async def get_me(
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get the authenticated user identity and associated workspaces."""
    stmt = text(
        """
        SELECT w.workspace_id, w.name, w.slug, w.plan_tier, r.name as role_name
        FROM workspace.memberships m
        JOIN workspace.workspaces w ON w.workspace_id = m.workspace_id
        LEFT JOIN identity.roles r ON r.role_id = m.role_id
        JOIN identity.users u ON u.user_id = m.user_id
        WHERE u.supabase_user_id = :uid OR u.email = :email
        """
    )
    result = await db.execute(stmt, {"uid": user.user_id, "email": user.email})
    workspaces = [
        {
            "workspace_id": row[0],
            "name": row[1],
            "slug": row[2],
            "plan_tier": row[3],
            "role": row[4] or "MEMBER",
        }
        for row in result.fetchall()
    ]

    return {
        "user_id": user.user_id,
        "email": user.email,
        "auth_method": user.auth_method,
        "workspaces": workspaces,
    }


class UpdateProfileRequest(BaseModel):
    full_name: str | None = Field(None, max_length=150)
    avatar_url: str | None = Field(None, max_length=500)


@router.patch("/me")
async def update_me(
    req: UpdateProfileRequest,
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update profile information for the authenticated user."""
    updates = []
    params: dict[str, Any] = {"uid": user.user_id, "email": user.email}

    if req.full_name is not None:
        updates.append("full_name = :full_name")
        params["full_name"] = req.full_name

    if req.avatar_url is not None:
        updates.append("avatar_url = :avatar_url")
        params["avatar_url"] = req.avatar_url

    if updates:
        stmt = f"UPDATE identity.users SET {', '.join(updates)} WHERE supabase_user_id = :uid OR email = :email"
        await db.execute(text(stmt), params)

    return {"updated": True}


@router.post("/api-keys")
async def create_api_key(
    req: CreateApiKeyRequest,
    user: UserClaims = Depends(get_current_user),
    membership: dict = Depends(require_permission("apikey:create")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Generate a new API key for workspace programmatic access."""
    # Resolve user UUID
    u_row = (
        await db.execute(
            text(
                "SELECT user_id FROM identity.users WHERE supabase_user_id = :uid OR email = :email"
            ),
            {"uid": user.user_id, "email": user.email},
        )
    ).fetchone()

    user_uuid = u_row[0] if u_row else uuid4()
    raw_secret = secrets.token_urlsafe(32)
    api_key = f"{settings.auth.api_key_prefix}{raw_secret}"
    key_hash = hash_api_key(api_key)
    key_prefix = api_key[:12]
    api_key_id = uuid4()

    await db.execute(
        text(
            """
            INSERT INTO identity.api_keys
            (api_key_id, user_id, workspace_id, key_hash, key_prefix, name, scopes)
            VALUES (:id, :uid, :ws_id, :kh, :kp, :name, :scopes)
            """
        ),
        {
            "id": api_key_id,
            "uid": user_uuid,
            "ws_id": req.workspace_id,
            "kh": key_hash,
            "kp": key_prefix,
            "name": req.name,
            "scopes": req.scopes,
        },
    )

    return {
        "api_key_id": str(api_key_id),
        "name": req.name,
        "key_prefix": key_prefix,
        "scopes": req.scopes,
        "secret_key": api_key,  # Note: displayed once on creation
    }


@router.get("/api-keys")
async def list_api_keys(
    workspace_id: str,
    membership: dict = Depends(require_permission("apikey:read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List non-revoked API keys for a workspace."""
    result = await db.execute(
        text(
            """
            SELECT api_key_id, name, key_prefix, scopes, created_at, last_used_at
            FROM identity.api_keys
            WHERE workspace_id = :ws_id AND is_revoked = FALSE
            ORDER BY created_at DESC
            """
        ),
        {"ws_id": workspace_id},
    )
    return [
        {
            "api_key_id": str(row[0]),
            "name": row[1],
            "key_prefix": row[2],
            "scopes": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
            "last_used_at": row[5].isoformat() if row[5] else None,
        }
        for row in result.fetchall()
    ]


@router.delete("/api-keys/{api_key_id}")
async def revoke_api_key(
    api_key_id: UUID,
    workspace_id: str,
    membership: dict = Depends(require_permission("apikey:delete")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Revoke an API key."""
    await db.execute(
        text(
            """
            UPDATE identity.api_keys
            SET is_revoked = TRUE
            WHERE api_key_id = :id AND workspace_id = :ws_id
            """
        ),
        {"id": api_key_id, "ws_id": workspace_id},
    )
    return {"revoked": True}
