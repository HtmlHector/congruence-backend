"""Worktree endpoints: task worktrees inside a project devbox."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.contexts.projects.application.service import ProjectService
from src.contexts.usage.application.service import UsageLimitError
from src.contexts.worktrees.application.service import WorktreeError, WorktreeService
from src.contexts.worktrees.domain.entities import Worktree, WorktreeStatus
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

router = APIRouter(tags=["Worktrees"])

_STATUS_CODE_MAP = {
    "PROJECT_NOT_READY": status.HTTP_400_BAD_REQUEST,
    "WORKTREE_NOT_READY": status.HTTP_400_BAD_REQUEST,
    "WORKTREE_LIMIT": status.HTTP_402_PAYMENT_REQUIRED,
    "USAGE_LIMIT": status.HTTP_402_PAYMENT_REQUIRED,
    "BOX_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
    "REPO_CLONE_FAILED": status.HTTP_502_BAD_GATEWAY,
    "SETUP_FAILED": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "RESTORE_WINDOW_EXPIRED": status.HTTP_410_GONE,
    "BOX_REBUILT": status.HTTP_409_CONFLICT,
    "BRANCH_EXISTS": status.HTTP_409_CONFLICT,
    "PRIMARY_WORKTREE": status.HTTP_409_CONFLICT,
    "INVALID_BRANCH": status.HTTP_400_BAD_REQUEST,
    "WORKTREE_CREATE_FAILED": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


class CreateWorktreeRequest(BaseModel):
    title: str
    base_branch: str | None = None
    branch: str | None = None
    initial_prompt: str | None = None


class UpdateWorktreeRequest(BaseModel):
    title: str | None = None
    status: WorktreeStatus | None = None
    pr_number: int | None = None
    pr_url: str | None = None


class WorktreeResponse(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    devbox_id: str
    title: str
    initial_prompt: str | None
    branch: str
    base_branch: str
    path: str
    slot: int
    port_base: int
    status: WorktreeStatus
    is_primary: bool
    pr_number: int | None
    pr_url: str | None
    created_by: str
    created_at: str
    deleted_at: str | None


def serialize(worktree: Worktree) -> WorktreeResponse:
    return WorktreeResponse(
        id=str(worktree.id),
        workspace_id=str(worktree.workspace_id),
        project_id=str(worktree.project_id),
        devbox_id=str(worktree.devbox_id),
        title=worktree.title,
        initial_prompt=worktree.initial_prompt,
        branch=worktree.branch,
        base_branch=worktree.base_branch,
        path=worktree.path,
        slot=worktree.slot,
        port_base=worktree.port_base,
        status=worktree.status,
        is_primary=worktree.is_primary,
        pr_number=worktree.pr_number,
        pr_url=worktree.pr_url,
        created_by=worktree.created_by,
        created_at=worktree.created_at.isoformat(),
        deleted_at=worktree.deleted_at.isoformat() if worktree.deleted_at else None,
    )


def require_workspace(tenant: TenantContext) -> UUID:
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )
    return tenant.workspace_uuid


def raise_worktree_error(exc: WorktreeError | UsageLimitError) -> None:
    code = getattr(exc, "code", "WORKTREE_CREATE_FAILED")
    raise HTTPException(
        status_code=_STATUS_CODE_MAP.get(code, status.HTTP_400_BAD_REQUEST),
        detail={"code": code, "message": str(exc)},
    )


@router.post(
    "/projects/{project_id}/worktrees",
    response_model=WorktreeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_worktree(
    project_id: UUID,
    payload: CreateWorktreeRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Create a worktree from a base branch and ready it for agents."""
    workspace_id = require_workspace(tenant)
    project = await ProjectService(session).get_project(project_id, workspace_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    service = WorktreeService(session)
    try:
        worktree = await service.create(
            project,
            tenant.user_id or "system",
            title=payload.title,
            base_branch=payload.base_branch,
            branch=payload.branch,
            initial_prompt=payload.initial_prompt,
        )
    except (WorktreeError, UsageLimitError) as exc:
        raise_worktree_error(exc)
    return serialize(worktree)


@router.post("/projects/{project_id}/worktrees/primary", response_model=WorktreeResponse)
async def ensure_primary_worktree(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Return the project's primary worktree, provisioning it when missing.

    Project creation already records the primary checkout; this idempotent call
    backfills projects created before that existed and is safe to retry.
    """
    workspace_id = require_workspace(tenant)
    project = await ProjectService(session).get_project(project_id, workspace_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    try:
        worktree = await WorktreeService(session).ensure_primary(
            project, tenant.user_id or "system"
        )
    except (WorktreeError, UsageLimitError) as exc:
        raise_worktree_error(exc)
    return serialize(worktree)


@router.get("/projects/{project_id}/worktrees", response_model=list[WorktreeResponse])
async def list_worktrees(
    project_id: UUID,
    include_deleted: bool = Query(False),
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List worktrees for a project."""
    workspace_id = require_workspace(tenant)
    service = WorktreeService(session)
    worktrees = await service.list_for_project(
        project_id, workspace_id, include_deleted=include_deleted
    )
    return [serialize(worktree) for worktree in worktrees]


@router.get("/worktrees/{worktree_id}", response_model=WorktreeResponse)
async def get_worktree(
    worktree_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Fetch one worktree."""
    workspace_id = require_workspace(tenant)
    worktree = await WorktreeService(session).get(worktree_id, workspace_id)
    if not worktree:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worktree not found")
    return serialize(worktree)


@router.patch("/worktrees/{worktree_id}", response_model=WorktreeResponse)
async def update_worktree(
    worktree_id: UUID,
    payload: UpdateWorktreeRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Update task metadata for a worktree."""
    workspace_id = require_workspace(tenant)
    service = WorktreeService(session)
    worktree = await service.get(worktree_id, workspace_id)
    if not worktree:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worktree not found")

    updated = await service.update(
        worktree,
        tenant.user_id or "system",
        title=payload.title,
        status=payload.status,
        pr_number=payload.pr_number,
        pr_url=payload.pr_url,
    )
    return serialize(updated)


@router.delete("/worktrees/{worktree_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_worktree(
    worktree_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a worktree and close its terminals."""
    workspace_id = require_workspace(tenant)
    service = WorktreeService(session)
    worktree = await service.get(worktree_id, workspace_id)
    if not worktree:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worktree not found")
    try:
        await service.delete(worktree, tenant.user_id or "system")
    except WorktreeError as exc:
        raise_worktree_error(exc)


@router.post("/worktrees/{worktree_id}/restore", response_model=WorktreeResponse)
async def restore_worktree(
    worktree_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Restore a soft-deleted worktree inside the 30-day window."""
    workspace_id = require_workspace(tenant)
    service = WorktreeService(session)
    worktree = await service.get(worktree_id, workspace_id)
    if not worktree:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worktree not found")
    try:
        restored = await service.restore(worktree, tenant.user_id or "system")
    except WorktreeError as exc:
        raise_worktree_error(exc)
    return serialize(restored)
