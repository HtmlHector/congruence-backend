"""Usage and entitlement endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.contexts.usage.application.service import UsageService
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

router = APIRouter(prefix="/usage", tags=["Usage"])


def _require_workspace(tenant: TenantContext) -> UUID:
    if not tenant.workspace_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace context"
        )
    return tenant.workspace_uuid


@router.get("")
async def get_usage(
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Return this period's metered usage and plan limits."""
    workspace_id = _require_workspace(tenant)
    return await UsageService(session).summary(workspace_id)


@router.get("/limits")
async def get_limits(
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Return the workspace's plan entitlements."""
    workspace_id = _require_workspace(tenant)
    limits = await UsageService(session).limits_for_workspace(workspace_id)
    return {
        "included_seconds": limits.included_seconds,
        "awake_boxes": limits.awake_boxes,
        "terminals_per_worktree": limits.terminals_per_worktree,
    }
