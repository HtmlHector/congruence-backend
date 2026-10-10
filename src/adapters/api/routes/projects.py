"""Projects sample feature endpoints demonstrating multi-tenancy and DDD."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.contexts.projects.application.service import ProjectService
from src.contexts.projects.domain.entities import ProjectStatus
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

router = APIRouter(prefix="/projects", tags=["Projects"])


class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None
    metadata: dict[str, Any] = {}


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    status: ProjectStatus | None = None
    metadata: dict[str, Any] | None = None


class ProjectResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str | None
    status: ProjectStatus
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Create a project in active workspace."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    service = ProjectService(session)
    project = await service.create_project(
        workspace_id=tenant.workspace_uuid,
        actor_id=tenant.user_id or "system",
        name=payload.name,
        description=payload.description,
        metadata=payload.metadata,
    )
    return {
        "id": str(project.id),
        "workspace_id": str(project.workspace_id),
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "metadata": project.metadata,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    status_filter: ProjectStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List projects belonging to the active workspace."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    service = ProjectService(session)
    projects = await service.list_projects(
        workspace_id=tenant.workspace_uuid,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return [
        {
            "id": str(p.id),
            "workspace_id": str(p.workspace_id),
            "name": p.name,
            "description": p.description,
            "status": p.status,
            "metadata": p.metadata,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
        }
        for p in projects
    ]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Fetch project by ID scoped to active workspace."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    service = ProjectService(session)
    project = await service.get_project(project_id, tenant.workspace_uuid)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return {
        "id": str(project.id),
        "workspace_id": str(project.workspace_id),
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "metadata": project.metadata,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    payload: UpdateProjectRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Update project in active workspace."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    service = ProjectService(session)
    updated = await service.update_project(
        project_id=project_id,
        workspace_id=tenant.workspace_uuid,
        actor_id=tenant.user_id or "system",
        name=payload.name,
        description=payload.description,
        status=payload.status,
        metadata=payload.metadata,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    return {
        "id": str(updated.id),
        "workspace_id": str(updated.workspace_id),
        "name": updated.name,
        "description": updated.description,
        "status": updated.status,
        "metadata": updated.metadata,
        "created_at": updated.created_at.isoformat(),
        "updated_at": updated.updated_at.isoformat(),
    }


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete project in active workspace."""
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )

    service = ProjectService(session)
    success = await service.delete_project(
        project_id=project_id,
        workspace_id=tenant.workspace_uuid,
        actor_id=tenant.user_id or "system",
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
