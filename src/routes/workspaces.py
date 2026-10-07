"""Workspaces, memberships, and invitations endpoints."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.workspace.models import generate_workspace_id
from src.infrastructure.auth.dependencies import (
    get_current_user,
    require_permission,
    require_workspace_member,
)
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.database.session import get_db
from src.infrastructure.security.audit import AuditLogger

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    slug: str | None = None


class UpdateWorkspaceRequest(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="MEMBER")


class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(..., min_length=2, max_length=50)


def slugify(raw_text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", raw_text.lower()).strip()
    return re.sub(r"[-\s]+", "-", slug)


@router.get("")
async def list_workspaces(
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List all workspaces the current user belongs to."""
    stmt = text(
        """
        SELECT w.workspace_id, w.name, w.slug, w.plan_tier, w.created_at, r.name as role
        FROM workspace.memberships m
        JOIN workspace.workspaces w ON w.workspace_id = m.workspace_id
        LEFT JOIN identity.roles r ON r.role_id = m.role_id
        JOIN identity.users u ON u.user_id = m.user_id
        WHERE (u.supabase_user_id = :uid OR u.email = :email) AND w.deleted_at IS NULL
        ORDER BY w.created_at DESC
        """
    )
    result = await db.execute(stmt, {"uid": user.user_id, "email": user.email})
    return [
        {
            "workspace_id": row[0],
            "name": row[1],
            "slug": row[2],
            "plan_tier": row[3],
            "created_at": row[4].isoformat()
            if hasattr(row[4], "isoformat")
            else (str(row[4]) if row[4] else None),
            "role": row[5] or "MEMBER",
        }
        for row in result.fetchall()
    ]


@router.post("")
async def create_workspace(
    req: CreateWorkspaceRequest,
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new workspace and assign the creator as OWNER."""
    workspace_id = generate_workspace_id()
    raw_slug = req.slug or slugify(req.name)
    slug = f"{raw_slug}-{workspace_id[-6:]}"

    user_row = (
        await db.execute(
            text(
                "SELECT user_id FROM identity.users WHERE supabase_user_id = :uid OR email = :email"
            ),
            {"uid": user.user_id, "email": user.email},
        )
    ).fetchone()

    if not user_row:
        user_uuid = uuid4()
        await db.execute(
            text(
                """
                INSERT INTO identity.users (user_id, supabase_user_id, email, full_name)
                VALUES (:uid, :sub, :email, :name)
                """
            ),
            {"uid": user_uuid, "sub": user.user_id, "email": user.email, "name": req.name},
        )
    else:
        user_uuid = user_row[0]

    await db.execute(
        text(
            """
            INSERT INTO workspace.workspaces (workspace_id, name, slug, plan_tier)
            VALUES (:ws_id, :name, :slug, 'FREE')
            """
        ),
        {"ws_id": workspace_id, "name": req.name, "slug": slug},
    )

    role_row = (
        await db.execute(text("SELECT role_id FROM identity.roles WHERE name = 'OWNER' LIMIT 1"))
    ).fetchone()
    owner_role_id = role_row[0] if role_row else uuid4()

    await db.execute(
        text(
            """
            INSERT INTO workspace.memberships (membership_id, user_id, workspace_id, role_id)
            VALUES (gen_random_uuid(), :uid, :ws_id, :role_id)
            """
        ),
        {"uid": user_uuid, "ws_id": workspace_id, "role_id": owner_role_id},
    )

    await db.execute(
        text("UPDATE identity.users SET last_workspace_id = :ws_id WHERE user_id = :uid"),
        {"ws_id": workspace_id, "uid": user_uuid},
    )

    await AuditLogger(db).log(
        workspace_id=workspace_id,
        actor_id=user.user_id,
        actor_type="USER",
        action="workspace.created",
        resource_type="workspace",
        resource_id=workspace_id,
        details={"name": req.name, "slug": slug},
    )

    return {
        "workspace_id": workspace_id,
        "name": req.name,
        "slug": slug,
        "plan_tier": "FREE",
        "role": "OWNER",
    }


@router.get("/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    membership: dict = Depends(require_workspace_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve workspace details."""
    row = (
        await db.execute(
            text(
                "SELECT workspace_id, name, slug, plan_tier, created_at FROM workspace.workspaces WHERE workspace_id = :ws_id AND deleted_at IS NULL"
            ),
            {"ws_id": workspace_id},
        )
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Workspace not found")

    return {
        "workspace_id": row[0],
        "name": row[1],
        "slug": row[2],
        "plan_tier": row[3],
        "created_at": row[4].isoformat() if row[4] else None,
        "role": membership["role"],
    }


@router.patch("/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    req: UpdateWorkspaceRequest,
    membership: dict = Depends(require_permission("workspace:write")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Update workspace attributes."""
    if req.name:
        await db.execute(
            text("UPDATE workspace.workspaces SET name = :name WHERE workspace_id = :ws_id"),
            {"name": req.name, "ws_id": workspace_id},
        )
        await AuditLogger(db).log(
            workspace_id=workspace_id,
            actor_id=membership["user_id"],
            actor_type="USER",
            action="workspace.updated",
            resource_type="workspace",
            resource_id=workspace_id,
            details={"name": req.name},
        )
    return {"updated": True}


@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    membership: dict = Depends(require_permission("workspace:delete")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Soft-delete workspace."""
    now = datetime.now(UTC)
    await db.execute(
        text("UPDATE workspace.workspaces SET deleted_at = :now WHERE workspace_id = :ws_id"),
        {"now": now, "ws_id": workspace_id},
    )
    await AuditLogger(db).log(
        workspace_id=workspace_id,
        actor_id=membership["user_id"],
        actor_type="USER",
        action="workspace.deleted",
        resource_type="workspace",
        resource_id=workspace_id,
    )
    return {"deleted": True}


# ── Members & Invitations ──────────────────────────────────────────────


@router.get("/{workspace_id}/members")
async def list_members(
    workspace_id: str,
    membership: dict = Depends(require_permission("member:read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List members of a workspace."""
    result = await db.execute(
        text(
            """
            SELECT m.membership_id, u.user_id, u.email, u.full_name, u.avatar_url, r.name as role, m.joined_at
            FROM workspace.memberships m
            JOIN identity.users u ON u.user_id = m.user_id
            LEFT JOIN identity.roles r ON r.role_id = m.role_id
            WHERE m.workspace_id = :ws_id
            ORDER BY m.joined_at ASC
            """
        ),
        {"ws_id": workspace_id},
    )
    return [
        {
            "membership_id": str(row[0]),
            "user_id": str(row[1]),
            "email": row[2],
            "full_name": row[3],
            "avatar_url": row[4],
            "role": row[5] or "MEMBER",
            "joined_at": row[6].isoformat() if row[6] else None,
        }
        for row in result.fetchall()
    ]


@router.patch("/{workspace_id}/members/{user_id}")
async def update_member_role(
    workspace_id: str,
    user_id: UUID,
    req: UpdateMemberRoleRequest,
    membership: dict = Depends(require_permission("member:write")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Change member's role."""
    target_role = (
        await db.execute(
            text("SELECT role_id FROM identity.roles WHERE name = :role LIMIT 1"),
            {"role": req.role.upper()},
        )
    ).fetchone()

    if not target_role:
        raise HTTPException(status_code=400, detail=f"Role '{req.role}' not found")

    await db.execute(
        text(
            """
            UPDATE workspace.memberships
            SET role_id = :role_id
            WHERE workspace_id = :ws_id AND user_id = :uid
            """
        ),
        {"role_id": target_role[0], "ws_id": workspace_id, "uid": user_id},
    )

    await AuditLogger(db).log(
        workspace_id=workspace_id,
        actor_id=membership["user_id"],
        actor_type="USER",
        action="member.role_updated",
        resource_type="membership",
        resource_id=str(user_id),
        details={"new_role": req.role.upper()},
    )
    return {"updated": True, "role": req.role.upper()}


@router.delete("/{workspace_id}/members/{user_id}")
async def remove_member(
    workspace_id: str,
    user_id: UUID,
    membership: dict = Depends(require_permission("member:write")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Remove a member from the workspace."""
    # Ensure not removing the sole OWNER
    owners = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM workspace.memberships m
                JOIN identity.roles r ON r.role_id = m.role_id
                WHERE m.workspace_id = :ws_id AND r.name = 'OWNER'
                """
            ),
            {"ws_id": workspace_id},
        )
    ).scalar() or 0

    mem_is_owner = (
        await db.execute(
            text(
                """
                SELECT 1 FROM workspace.memberships m
                JOIN identity.roles r ON r.role_id = m.role_id
                WHERE m.workspace_id = :ws_id AND m.user_id = :uid AND r.name = 'OWNER'
                """
            ),
            {"ws_id": workspace_id, "uid": user_id},
        )
    ).fetchone()

    if mem_is_owner and owners <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the only owner of a workspace")

    await db.execute(
        text("DELETE FROM workspace.memberships WHERE workspace_id = :ws_id AND user_id = :uid"),
        {"ws_id": workspace_id, "uid": user_id},
    )

    await AuditLogger(db).log(
        workspace_id=workspace_id,
        actor_id=membership["user_id"],
        actor_type="USER",
        action="member.removed",
        resource_type="membership",
        resource_id=str(user_id),
    )
    return {"removed": True}


@router.post("/{workspace_id}/invitations")
async def create_invitation(
    workspace_id: str,
    req: InviteMemberRequest,
    membership: dict = Depends(require_permission("member:write")),
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Invite a new member via email."""
    token = secrets.token_urlsafe(32)
    invitation_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=7)

    # Get caller's internal user UUID
    u_row = (
        await db.execute(
            text(
                "SELECT user_id FROM identity.users WHERE supabase_user_id = :uid OR email = :email"
            ),
            {"uid": user.user_id, "email": user.email},
        )
    ).fetchone()
    invited_by_id = u_row[0] if u_row else uuid4()

    # Find role
    role_row = (
        await db.execute(
            text("SELECT role_id FROM identity.roles WHERE name = :role LIMIT 1"),
            {"role": req.role.upper()},
        )
    ).fetchone()
    role_id = role_row[0] if role_row else uuid4()

    await db.execute(
        text(
            """
            INSERT INTO workspace.invitations
            (invitation_id, workspace_id, invited_by, email, role_id, token, status, expires_at)
            VALUES (:id, :ws_id, :by, :email, :role_id, :token, 'PENDING', :exp)
            """
        ),
        {
            "id": invitation_id,
            "ws_id": workspace_id,
            "by": invited_by_id,
            "email": str(req.email),
            "role_id": role_id,
            "token": token,
            "exp": expires_at,
        },
    )

    await AuditLogger(db).log(
        workspace_id=workspace_id,
        actor_id=user.user_id,
        actor_type="USER",
        action="invitation.created",
        resource_type="invitation",
        resource_id=str(invitation_id),
        details={"email": str(req.email), "role": req.role.upper()},
    )

    return {
        "invitation_id": str(invitation_id),
        "email": str(req.email),
        "role": req.role.upper(),
        "token": token,
        "expires_at": expires_at.isoformat(),
    }


@router.get("/{workspace_id}/invitations")
async def list_invitations(
    workspace_id: str,
    membership: dict = Depends(require_permission("member:read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List pending invitations for workspace."""
    result = await db.execute(
        text(
            """
            SELECT i.invitation_id, i.email, r.name as role, i.status, i.expires_at, i.created_at
            FROM workspace.invitations i
            LEFT JOIN identity.roles r ON r.role_id = i.role_id
            WHERE i.workspace_id = :ws_id AND i.status = 'PENDING'
            ORDER BY i.created_at DESC
            """
        ),
        {"ws_id": workspace_id},
    )
    return [
        {
            "invitation_id": str(row[0]),
            "email": row[1],
            "role": row[2] or "MEMBER",
            "status": row[3],
            "expires_at": row[4].isoformat() if row[4] else None,
            "created_at": row[5].isoformat() if row[5] else None,
        }
        for row in result.fetchall()
    ]


@router.delete("/{workspace_id}/invitations/{invitation_id}")
async def revoke_invitation(
    workspace_id: str,
    invitation_id: UUID,
    membership: dict = Depends(require_permission("member:write")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Revoke a pending invitation."""
    await db.execute(
        text(
            "UPDATE workspace.invitations SET status = 'REVOKED' WHERE invitation_id = :id AND workspace_id = :ws_id"
        ),
        {"id": invitation_id, "ws_id": workspace_id},
    )
    return {"revoked": True}


@router.post("/invitations/{token}/accept")
async def accept_invitation(
    token: str,
    user: UserClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Accept an invitation to join a workspace."""
    inv = (
        await db.execute(
            text(
                """
                SELECT invitation_id, workspace_id, role_id, email, status, expires_at
                FROM workspace.invitations
                WHERE token = :token
                """
            ),
            {"token": token},
        )
    ).fetchone()

    if not inv:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if inv[4] != "PENDING":
        raise HTTPException(status_code=400, detail=f"Invitation already {inv[4].lower()}")

    if inv[5] < datetime.now(UTC):
        await db.execute(
            text("UPDATE workspace.invitations SET status = 'EXPIRED' WHERE token = :token"),
            {"token": token},
        )
        raise HTTPException(status_code=400, detail="Invitation expired")

    # Resolve or create user in identity.users
    user_row = (
        await db.execute(
            text(
                "SELECT user_id FROM identity.users WHERE supabase_user_id = :uid OR email = :email"
            ),
            {"uid": user.user_id, "email": user.email},
        )
    ).fetchone()

    if not user_row:
        user_uuid = uuid4()
        await db.execute(
            text(
                """
                INSERT INTO identity.users (user_id, supabase_user_id, email, full_name)
                VALUES (:uid, :sub, :email, 'Workspace Member')
                """
            ),
            {"uid": user_uuid, "sub": user.user_id, "email": user.email},
        )
    else:
        user_uuid = user_row[0]

    # Create membership
    await db.execute(
        text(
            """
            INSERT INTO workspace.memberships (membership_id, user_id, workspace_id, role_id)
            VALUES (gen_random_uuid(), :uid, :ws_id, :role_id)
            ON CONFLICT (user_id, workspace_id) DO UPDATE SET role_id = EXCLUDED.role_id
            """
        ),
        {"uid": user_uuid, "ws_id": inv[1], "role_id": inv[2]},
    )

    # Mark accepted
    await db.execute(
        text("UPDATE workspace.invitations SET status = 'ACCEPTED' WHERE token = :token"),
        {"token": token},
    )

    await AuditLogger(db).log(
        workspace_id=inv[1],
        actor_id=user.user_id,
        actor_type="USER",
        action="invitation.accepted",
        resource_type="invitation",
        resource_id=str(inv[0]),
    )

    return {"accepted": True, "workspace_id": inv[1]}
