"""Devbox endpoints: inspect, pause, resume, and reset a project's machine."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.contexts.devbox.application.service import DevboxError, DevboxService
from src.contexts.devbox.domain.entities import Devbox
from src.contexts.projects.application.service import ProjectService
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

router = APIRouter(tags=["Devboxes"])


class DevboxResponse(BaseModel):
    id: str
    project_id: str
    provider: str
    region: str
    status: str
    image_ref: str
    cpu: int
    memory_mb: int
    disk_gb: int
    awake_seconds_total: int
    error_code: str | None
    last_activity_at: str | None


def serialize(box: Devbox) -> DevboxResponse:
    return DevboxResponse(
        id=str(box.id),
        project_id=str(box.project_id),
        provider=box.provider,
        region=box.region,
        status=box.status.value,
        image_ref=box.image_ref,
        cpu=box.cpu,
        memory_mb=box.memory_mb,
        disk_gb=box.disk_gb,
        awake_seconds_total=box.awake_seconds_total,
        error_code=box.error_code,
        last_activity_at=(box.last_activity_at.isoformat() if box.last_activity_at else None),
    )


def _require_workspace(tenant: TenantContext) -> UUID:
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )
    return tenant.workspace_uuid


async def _project_or_404(project_id: UUID, tenant: TenantContext, session: AsyncSession) -> Any:
    workspace_id = _require_workspace(tenant)
    project = await ProjectService(session).get_project(project_id, workspace_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.get("/projects/{project_id}/box", response_model=DevboxResponse)
async def get_box(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Return the project's live devbox."""
    project = await _project_or_404(project_id, tenant, session)
    box = await DevboxService(session).get_live(project.id, project.workspace_id)
    if not box:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Devbox not found")
    return serialize(box)


@router.post("/projects/{project_id}/box/pause", response_model=DevboxResponse)
async def pause_box(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Pause a project devbox, retaining its disk."""
    project = await _project_or_404(project_id, tenant, session)
    service = DevboxService(session)
    box = await service.get_live(project.id, project.workspace_id)
    if not box:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Devbox not found")
    return serialize(await service.pause(box, tenant.user_id or "system"))


@router.post("/projects/{project_id}/box/resume", response_model=DevboxResponse)
async def resume_box(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Resume a paused project devbox."""
    project = await _project_or_404(project_id, tenant, session)
    service = DevboxService(session)
    box = await service.get_live(project.id, project.workspace_id)
    if not box:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Devbox not found")
    return serialize(await service.resume(box, tenant.user_id or "system"))


@router.post("/projects/{project_id}/box/reset", response_model=DevboxResponse)
async def reset_box(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Rebuild a project's devbox from the pinned base image."""
    project = await _project_or_404(project_id, tenant, session)
    try:
        box = await DevboxService(session).reset(project, tenant.user_id or "system")
    except DevboxError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return serialize(box)
