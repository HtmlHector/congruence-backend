"""FastAPI dependencies for JWT authentication, API keys, and workspace RLS binding."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from uuid import UUID

import structlog
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.identity.tenant import TenantContext
from src.infrastructure.auth.jwt import UserClaims, decode_jwt_token
from src.infrastructure.database.session import get_db
from src.infrastructure.database.tenant_scope import bind_rls_workspace

logger = structlog.get_logger(__name__)

security = HTTPBearer(auto_error=False)


def hash_api_key(token: str) -> str:
    """SHA-256 hash an API key for constant-time lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _verify_api_key(token: str, db: AsyncSession) -> UserClaims | None:
    prefix = settings.auth.api_key_prefix
    if not token.startswith(prefix):
        return None

    key_hash = hash_api_key(token)
    stmt = text(
        """
        SELECT ak.api_key_id, ak.user_id, ak.workspace_id, ak.scopes, u.email
        FROM identity.api_keys ak
        JOIN identity.users u ON u.user_id = ak.user_id
        WHERE ak.key_hash = :key_hash AND ak.is_revoked = FALSE
          AND (ak.expires_at IS NULL OR ak.expires_at > now())
        """
    )
    result = await db.execute(stmt, {"key_hash": key_hash})
    row = result.fetchone()
    if not row:
        return None

    # Update last_used_at
    await db.execute(
        text("UPDATE identity.api_keys SET last_used_at = now() WHERE key_hash = :key_hash"),
        {"key_hash": key_hash},
    )

    return UserClaims(
        user_id=str(row[1]),
        email=str(row[4]),
        auth_method="api_key",
        workspace_id=str(row[2]),
        role="api_key",
        scopes=list(row[3] or []),
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> UserClaims:
    """Validate Bearer token (JWT or API Key) and return UserClaims."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication credentials required")

    token = credentials.credentials.strip()

    # API key check
    if token.startswith(settings.auth.api_key_prefix):
        claims = await _verify_api_key(token, db)
        if claims is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        return claims

    # JWT check
    try:
        claims = decode_jwt_token(token)
        return claims
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


async def get_current_user_or_none(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> UserClaims | None:
    """Optional authentication — returns None if credentials missing or invalid."""
    if credentials is None:
        return None
    try:
        return await get_current_user(request, credentials, db)
    except HTTPException:
        return None


async def get_current_user_id(
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UUID:
    """Resolve the internal user_id UUID for the authenticated user."""
    stmt = text(
        "SELECT user_id FROM identity.users WHERE supabase_user_id = :uid OR email = :email"
    )
    result = await db.execute(stmt, {"uid": user.user_id, "email": user.email})
    row = result.fetchone()
    if row is not None:
        return row[0]
    try:
        return UUID(user.user_id)
    except Exception:
        raise HTTPException(status_code=404, detail="User record not found")


async def check_workspace_membership(
    db: AsyncSession,
    user: UserClaims,
    workspace_id: str,
) -> dict:
    """Verify the caller is a member of ``workspace_id``.

    Raises 403 on any mismatch. There is deliberately no development
    auto-heal here: in debug mode this previously granted OWNER on any
    workspace to any authenticated user, which defeated tenancy entirely.
    """
    # If authenticated via workspace-scoped API key, check match
    if user.auth_method == "api_key" and user.workspace_id and user.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="API key not authorized for this workspace")

    stmt = text(
        """
        SELECT r.name, r.permissions
        FROM workspace.memberships m
        LEFT JOIN identity.roles r ON r.role_id = m.role_id
        LEFT JOIN identity.users u ON u.user_id = m.user_id
        WHERE m.workspace_id = :ws_id
          AND (u.supabase_user_id = :uid OR u.email = :email OR CAST(m.user_id AS TEXT) = :uid)
        """
    )
    result = await db.execute(
        stmt, {"ws_id": workspace_id, "uid": user.user_id, "email": user.email}
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    role_name = row[0] or "MEMBER"
    permissions = row[1] or []

    return {
        "user_id": user.user_id,
        "workspace_id": workspace_id,
        "role": role_name,
        "permissions": permissions,
    }


async def require_workspace_member(
    workspace_id: str,
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """FastAPI dependency: verifies workspace membership and binds RLS scope."""
    membership = await check_workspace_membership(db, user, workspace_id)

    # Bind transaction RLS context
    await bind_rls_workspace(
        db,
        TenantContext(
            workspace_id=workspace_id,
            principal_id=user.user_id,
            auth_method=user.auth_method,
        ),
    )

    return membership


def require_permission(permission: str) -> Callable:
    """FastAPI dependency factory: verifies user holds specific permission."""

    async def checker(
        workspace_id: str,
        membership: dict = Depends(require_workspace_member),
    ) -> dict:
        perms = membership.get("permissions", [])
        if "*" in perms or "workspace:*" in perms or permission in perms:
            return membership
        # Check wildcard resource:*
        if ":" in permission:
            res = permission.split(":", 1)[0]
            if f"{res}:*" in perms:
                return membership
        raise HTTPException(status_code=403, detail=f"Missing required permission: {permission}")

    return checker
