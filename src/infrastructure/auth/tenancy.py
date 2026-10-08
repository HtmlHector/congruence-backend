"""Cross-resource workspace tenancy checks.

Projects belong to organizations; workspaces map to organizations via
memberships. Every route that takes a project or lane identifier must
authorize through these helpers instead of trusting the path parameter.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.congruence.models import Host, Lane, OrganizationMember, Project
from src.infrastructure.auth.jwt import UserClaims


async def user_has_project_access(
    db: AsyncSession, user: UserClaims, project_id: str
) -> bool:
    """True when the caller is a member of the project's organization."""
    res = await db.execute(
        select(Project.id)
        .join(
            OrganizationMember,
            OrganizationMember.organization_id == Project.organization_id,
        )
        .where(Project.id == project_id, OrganizationMember.user_id == user.user_id)
    )
    return res.first() is not None


async def require_project_access(
    db: AsyncSession, user: UserClaims, project_id: str
) -> None:
    """Raise 404 when the project does not exist or is outside the caller's org.

    404 (not 403) matches the existing convention: project existence is not
    disclosed across tenants.
    """
    if not await user_has_project_access(db, user, project_id):
        raise HTTPException(status_code=404, detail="Project not found")


async def lane_access(
    db: AsyncSession, user: UserClaims, lane_id: str
) -> bool | None:
    """Resolve tenancy for a lane.

    Returns True when authorized, False when the lane belongs to another
    tenant, and None when no lane row exists (so there is no tenant resource
    to protect — callers decide whether authentication alone suffices).
    """
    lane = (
        await db.execute(select(Lane).where(Lane.id == lane_id))
    ).scalar_one_or_none()
    if lane is None:
        return None

    host = (
        await db.execute(select(Host).where(Host.id == lane.host_id))
    ).scalar_one_or_none()
    if host is None:
        # Lane exists but cannot be attributed — fail closed.
        return False

    project = (
        await db.execute(select(Project).where(Project.id == host.project_id))
    ).scalar_one_or_none()
    if project is None:
        return False

    return await user_has_project_access(db, user, project.id)


async def require_lane_access(
    db: AsyncSession, user: UserClaims, lane_id: str
) -> None:
    """Raise 404 unless the caller may touch this lane (unknown lane = 404)."""
    access = await lane_access(db, user, lane_id)
    if access is not True:
        raise HTTPException(status_code=404, detail="Lane not found")
