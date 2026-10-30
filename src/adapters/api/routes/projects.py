"""Project endpoints: repository binding, devbox provisioning, and setup state."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.contexts.projects.application.service import ProjectService
from src.contexts.projects.domain.entities import Project, ProjectStatus
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

router = APIRouter(prefix="/projects", tags=["Projects"])


class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    repository_id: str | None = None
    repo_provider: Literal["github", "gitlab", "bitbucket"] = "github"
    default_branch: str = "main"
    root_path: str = ""
    setup_command: str | None = None


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    status: ProjectStatus | None = None
    metadata: dict[str, Any] | None = None
    default_branch: str | None = None
    setup_command: str | None = None


class ProjectResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str | None
    status: ProjectStatus
    metadata: dict[str, Any]
    repository_id: str | None
    repo_provider: str
    default_branch: str
    root_path: str
    setup_command: str | None
    setup_status: str
    base_image_ref: str | None
    created_at: str
    updated_at: str


def _serialize(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=str(project.id),
        workspace_id=str(project.workspace_id),
        name=project.name,
        description=project.description,
        status=project.status,
        metadata=project.metadata,
        repository_id=project.repository_id,
        repo_provider=project.repo_provider,
        default_branch=project.default_branch,
        root_path=project.root_path,
        setup_command=project.setup_command,
        setup_status=project.setup_status.value,
        base_image_ref=project.base_image_ref,
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
    )


def _require_workspace(tenant: TenantContext) -> UUID:
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )
    return tenant.workspace_uuid


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProjectRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Create a project and provision its devbox when a repository is bound."""
    workspace_id = _require_workspace(tenant)
    service = ProjectService(session)
    project = await service.create_project(
        workspace_id=workspace_id,
        actor_id=tenant.user_id or "system",
        name=payload.name,
        description=payload.description,
        metadata=payload.metadata,
        repository_id=payload.repository_id,
        repo_provider=payload.repo_provider,
        default_branch=payload.default_branch,
        root_path=payload.root_path,
        setup_command=payload.setup_command,
    )
    return _serialize(project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    status_filter: ProjectStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List projects belonging to the active workspace."""
    workspace_id = _require_workspace(tenant)
    service = ProjectService(session)
    projects = await service.list_projects(
        workspace_id=workspace_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return [_serialize(project) for project in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Fetch a project scoped to the active workspace."""
    workspace_id = _require_workspace(tenant)
    project = await ProjectService(session).get_project(project_id, workspace_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return _serialize(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    payload: UpdateProjectRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Update project metadata or setup configuration."""
    workspace_id = _require_workspace(tenant)
    service = ProjectService(session)
    updated = await service.update_project(
        project_id=project_id,
        workspace_id=workspace_id,
        actor_id=tenant.user_id or "system",
        name=payload.name,
        description=payload.description,
        status=payload.status,
        metadata=payload.metadata,
        default_branch=payload.default_branch,
        setup_command=payload.setup_command,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return _serialize(updated)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a project in the active workspace."""
    workspace_id = _require_workspace(tenant)
    service = ProjectService(session)
    success = await service.delete_project(
        project_id=project_id,
        workspace_id=workspace_id,
        actor_id=tenant.user_id or "system",
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
