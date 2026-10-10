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
from src.contexts.workspace.domain.entities import WorkspaceRole
from src.infrastructure.database.session import get_db

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


class CreateWorkspaceRequest(BaseModel):
    name: str


class AddMemberRequest(BaseModel):
    user_id: str
    role: WorkspaceRole = WorkspaceRole.MEMBER


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    slug: str
    stripe_customer_id: str | None


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: CreateWorkspaceRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Create a new workspace with current user as owner."""
    ws_svc = WorkspaceService(session)
    ws = await ws_svc.create_workspace(name=payload.name, owner_id=user.id)
    return {
        "id": str(ws.id),
        "name": ws.name,
        "slug": ws.slug,
        "stripe_customer_id": ws.stripe_customer_id,
    }


@router.get("", response_model=list[WorkspaceResponse])
async def list_my_workspaces(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List all workspaces the current user is a member of."""
    ws_svc = WorkspaceService(session)
    workspaces = await ws_svc.list_user_workspaces(user.id)
    return [
        {
            "id": str(w.id),
            "name": w.name,
            "slug": w.slug,
            "stripe_customer_id": w.stripe_customer_id,
        }
        for w in workspaces
    ]


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
