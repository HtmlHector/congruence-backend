"""Environment variable endpoints (write-only secrets)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.contexts.environments.application.service import EnvError, EnvService
from src.contexts.environments.domain.entities import EnvScope
from src.contexts.projects.application.service import ProjectService
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

router = APIRouter(tags=["Environments"])


class EnvVarRequest(BaseModel):
    key: str
    value: str
    is_secret: bool = True


class EnvVarResponse(BaseModel):
    key: str
    value: str
    is_secret: bool
    updated_by: str


def _require_workspace(tenant: TenantContext) -> UUID:
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )
    return tenant.workspace_uuid


async def _project_scope(
    project_id: UUID, tenant: TenantContext, session: AsyncSession
) -> tuple[UUID, UUID]:
    workspace_id = _require_workspace(tenant)
    project = await ProjectService(session).get_project(project_id, workspace_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return workspace_id, project_id


@router.get("/projects/{project_id}/env", response_model=list[EnvVarResponse])
async def list_project_env(
    project_id: UUID,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List project variables with secret values masked."""
    workspace_id, _ = await _project_scope(project_id, tenant, session)
    return await EnvService(session).list_scope(
        workspace_id=workspace_id, scope=EnvScope.PROJECT, project_id=project_id
    )


@router.put("/projects/{project_id}/env", response_model=list[EnvVarResponse])
async def upsert_project_env(
    project_id: UUID,
    payload: EnvVarRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Create or update one project variable."""
    workspace_id, _ = await _project_scope(project_id, tenant, session)
    service = EnvService(session)
    try:
        await service.upsert(
            workspace_id=workspace_id,
            scope=EnvScope.PROJECT,
            project_id=project_id,
            key=payload.key,
            value=payload.value,
            is_secret=payload.is_secret,
            actor_id=tenant.user_id or "system",
        )
    except EnvError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await service.list_scope(
        workspace_id=workspace_id, scope=EnvScope.PROJECT, project_id=project_id
    )


@router.delete("/projects/{project_id}/env/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_env(
    project_id: UUID,
    key: str,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a project variable."""
    workspace_id, _ = await _project_scope(project_id, tenant, session)
    deleted = await EnvService(session).delete(
        workspace_id=workspace_id,
        scope=EnvScope.PROJECT,
        project_id=project_id,
        key=key,
        actor_id=tenant.user_id or "system",
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Environment variable not found"
        )


@router.get("/env/workspace", response_model=list[EnvVarResponse])
async def list_workspace_env(
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """List workspace variables with secret values masked."""
    workspace_id = _require_workspace(tenant)
    return await EnvService(session).list_scope(workspace_id=workspace_id, scope=EnvScope.WORKSPACE)


@router.put("/env/workspace", response_model=list[EnvVarResponse])
async def upsert_workspace_env(
    payload: EnvVarRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Create or update one workspace variable."""
    workspace_id = _require_workspace(tenant)
    service = EnvService(session)
    try:
        await service.upsert(
            workspace_id=workspace_id,
            scope=EnvScope.WORKSPACE,
            project_id=None,
            key=payload.key,
            value=payload.value,
            is_secret=payload.is_secret,
            actor_id=tenant.user_id or "system",
        )
    except EnvError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await service.list_scope(workspace_id=workspace_id, scope=EnvScope.WORKSPACE)
