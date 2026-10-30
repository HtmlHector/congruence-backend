"""Workspace application service."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.billing.infrastructure.repository import SubscriptionRepository
from src.contexts.workspace.domain.entities import Workspace, WorkspaceMember, WorkspaceRole
from src.contexts.workspace.infrastructure.repository import WorkspaceRepository


def slugify(text: str) -> str:
    """Generate URL-safe slug from text."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", slug).strip("-")


class WorkspaceService:
    """Manages workspace lifecycle and team memberships."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = WorkspaceRepository(session)

    async def create_workspace(self, name: str, owner_id: UUID) -> Workspace:
        """Create a new workspace and designate creator as OWNER."""
        base_slug = slugify(name)
        slug = base_slug
        count = 1
        while await self._repo.get_by_slug(slug):
            slug = f"{base_slug}-{count}"
            count += 1

        workspace = Workspace(name=name, slug=slug)
        workspace.members.append(
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=owner_id,
                role=WorkspaceRole.OWNER,
            )
        )
        return await self._repo.add(workspace)

    async def get_workspace(self, workspace_id: UUID) -> Workspace | None:
        """Fetch workspace by ID."""
        return await self._repo.get_by_id(workspace_id)

    async def list_user_workspaces(self, user_id: UUID) -> list[Workspace]:
        """Fetch all workspaces the user is a member of, newest membership first."""
        workspaces = await self._repo.list_for_user(user_id)
        return list(workspaces)

    async def get_workspace_for_user(
        self, workspace_id: UUID, user_id: UUID
    ) -> tuple[Workspace, WorkspaceRole] | None:
        """Fetch a workspace along with the user's role, or None if not a member."""
        role = await self._repo.get_member_role(workspace_id, user_id)
        if role is None:
            return None
        workspace = await self._repo.get_by_id(workspace_id)
        if workspace is None:
            return None
        return workspace, role

    async def update_workspace_name(self, workspace_id: UUID, name: str) -> Workspace | None:
        """Rename a workspace, keeping its slug stable."""
        workspace = await self._repo.get_by_id(workspace_id)
        if workspace is None:
            return None
        workspace.name = name
        workspace.mark_updated()
        return await self._repo.update(workspace)

    async def delete_workspace(self, workspace_id: UUID) -> bool:
        """Delete a workspace and its cascading members, projects and subscription."""
        return await self._repo.delete(workspace_id)

    async def get_plan_tier(self, workspace_id: UUID) -> str:
        """Return the workspace billing tier, defaulting to free without a subscription."""
        subscription = await SubscriptionRepository(self._session).get_by_workspace_id(workspace_id)
        return subscription.tier.value if subscription else "free"

    async def add_member(
        self, workspace_id: UUID, user_id: UUID, role: WorkspaceRole = WorkspaceRole.MEMBER
    ) -> WorkspaceMember:
        """Add user to workspace with specified role."""
        return await self._repo.add_member(workspace_id, user_id, role)
