"""Workspace and multi-tenant organization endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_current_user
from src.contexts.identity.domain.entities import User
from src.contexts.workspace.application.service import WorkspaceService
from src.contexts.workspace.domain.entities import Workspace, WorkspaceRole
from src.infrastructure.database.rls import set_rls_workspace
from src.infrastructure.database.session import get_db

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])

WRITE_ROLES = (WorkspaceRole.OWNER, WorkspaceRole.ADMIN)


class CreateWorkspaceRequest(BaseModel):
    name: str


class UpdateWorkspaceRequest(BaseModel):
    name: str | None = None


class AddMemberRequest(BaseModel):
    user_id: str
    role: WorkspaceRole = WorkspaceRole.MEMBER


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    slug: str
    stripe_customer_id: str | None
    plan_tier: str
    created_at: str


def _serialize(workspace: Workspace, plan_tier: str) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=str(workspace.id),
        name=workspace.name,
        slug=workspace.slug,
        stripe_customer_id=workspace.stripe_customer_id,
        plan_tier=plan_tier,
        created_at=workspace.created_at.isoformat(),
    )


async def _require_member(
    service: WorkspaceService, workspace_id: UUID, user_id: UUID
) -> tuple[Workspace, WorkspaceRole]:
    """Fetch the workspace for a member, hiding its existence from outsiders."""
    result = await service.get_workspace_for_user(workspace_id, user_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    return result


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: CreateWorkspaceRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Create a new workspace with current user as owner."""
    ws_svc = WorkspaceService(session)
    ws = await ws_svc.create_workspace(name=payload.name, owner_id=user.id)
    return _serialize(ws, "free")


@router.get("", response_model=list[WorkspaceResponse])
async def list_my_workspaces(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List all workspaces the current user is a member of."""
    ws_svc = WorkspaceService(session)
    workspaces = await ws_svc.list_user_workspaces(user.id)
    return [_serialize(ws, await ws_svc.get_plan_tier(ws.id)) for ws in workspaces]


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Fetch a single workspace the current user belongs to."""
    ws_svc = WorkspaceService(session)
    workspace, _role = await _require_member(ws_svc, workspace_id, user.id)
    await set_rls_workspace(session, str(workspace_id))
    return _serialize(workspace, await ws_svc.get_plan_tier(workspace_id))


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: UUID,
    payload: UpdateWorkspaceRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Rename a workspace. Requires owner or admin role."""
    ws_svc = WorkspaceService(session)
    _workspace, role = await _require_member(ws_svc, workspace_id, user.id)
    if role not in WRITE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Requires owner or admin role"
        )
    if payload.name is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Nothing to update"
        )

    updated = await ws_svc.update_workspace_name(workspace_id, payload.name)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    await set_rls_workspace(session, str(workspace_id))
    return _serialize(updated, await ws_svc.get_plan_tier(workspace_id))


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a workspace. Requires owner role."""
    ws_svc = WorkspaceService(session)
    _workspace, role = await _require_member(ws_svc, workspace_id, user.id)
    if role is not WorkspaceRole.OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Requires owner role")
    await set_rls_workspace(session, str(workspace_id))
    await ws_svc.delete_workspace(workspace_id)


@router.post("/{workspace_id}/members", status_code=status.HTTP_201_CREATED)
async def add_workspace_member(
    workspace_id: UUID,
    payload: AddMemberRequest,
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Add a user as a member to a workspace."""
    ws_svc = WorkspaceService(session)
    try:
        target_user_id = UUID(payload.user_id)
        member = await ws_svc.add_member(workspace_id, target_user_id, payload.role)
        return {
            "id": str(member.id),
            "workspace_id": str(member.workspace_id),
            "user_id": str(member.user_id),
            "role": member.role.value,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to add member: {exc}",
        )
