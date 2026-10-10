"""Workspace application service."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

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
        """Fetch all workspaces the user is a member of."""
        workspaces = await self._repo.list_for_user(user_id)
        return list(workspaces)

    async def add_member(
        self, workspace_id: UUID, user_id: UUID, role: WorkspaceRole = WorkspaceRole.MEMBER
    ) -> WorkspaceMember:
        """Add user to workspace with specified role."""
        return await self._repo.add_member(workspace_id, user_id, role)
