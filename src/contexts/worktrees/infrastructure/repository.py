"""Worktree repository implementation."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.worktrees.domain.entities import Worktree
from src.contexts.worktrees.infrastructure.models import WorktreeModel
from src.shared.tenant_repository import TenantScopedRepository


class WorktreeRepository(TenantScopedRepository[WorktreeModel, Worktree]):
    """SQLAlchemy persistence for worktrees scoped by workspace."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    @property
    def _model_cls(self) -> type[WorktreeModel]:
        return WorktreeModel

    def _to_entity(self, model: WorktreeModel) -> Worktree:
        return Worktree(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            project_id=model.project_id,
            devbox_id=model.devbox_id,
            title=model.title,
            initial_prompt=model.initial_prompt,
            branch=model.branch,
            base_branch=model.base_branch,
            base_sha=model.base_sha,
            head_sha=model.head_sha,
            path=model.path,
            slot=model.slot,
            port_base=model.port_base,
            status=model.status,
            is_primary=model.is_primary,
            pr_number=model.pr_number,
            pr_url=model.pr_url,
            merged_at=model.merged_at,
            created_by=model.created_by,
            deleted_at=model.deleted_at,
        )

    def _to_model(self, entity: Worktree) -> WorktreeModel:
        return WorktreeModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            project_id=entity.project_id,
            devbox_id=entity.devbox_id,
            title=entity.title,
            initial_prompt=entity.initial_prompt,
            branch=entity.branch,
            base_branch=entity.base_branch,
            base_sha=entity.base_sha,
            head_sha=entity.head_sha,
            path=entity.path,
            slot=entity.slot,
            port_base=entity.port_base,
            status=entity.status,
            is_primary=entity.is_primary,
            pr_number=entity.pr_number,
            pr_url=entity.pr_url,
            merged_at=entity.merged_at,
            created_by=entity.created_by,
            deleted_at=entity.deleted_at,
        )

    async def list_for_project(
        self, project_id: UUID, workspace_id: UUID, *, include_deleted: bool = False
    ) -> Sequence[Worktree]:
        """List worktrees for a project, primary checkout first."""
        query = (
            select(WorktreeModel)
            .where(
                WorktreeModel.project_id == project_id,
                WorktreeModel.workspace_id == workspace_id,
            )
            .order_by(WorktreeModel.is_primary.desc(), WorktreeModel.created_at.asc())
        )
        if not include_deleted:
            query = query.where(WorktreeModel.deleted_at.is_(None))
        result = await self._session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]

    async def get_for_project(
        self, worktree_id: UUID, project_id: UUID, workspace_id: UUID
    ) -> Worktree | None:
        """Fetch a worktree strictly within a project and workspace."""
        query = select(WorktreeModel).where(
            WorktreeModel.id == worktree_id,
            WorktreeModel.project_id == project_id,
            WorktreeModel.workspace_id == workspace_id,
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_primary(self, project_id: UUID, workspace_id: UUID) -> Worktree | None:
        """Return the project's live primary (root checkout) worktree."""
        query = select(WorktreeModel).where(
            WorktreeModel.project_id == project_id,
            WorktreeModel.workspace_id == workspace_id,
            WorktreeModel.is_primary.is_(True),
            WorktreeModel.deleted_at.is_(None),
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_branch(
        self, project_id: UUID, workspace_id: UUID, branch: str
    ) -> Worktree | None:
        """Return the live worktree currently holding a branch, if any."""
        query = select(WorktreeModel).where(
            WorktreeModel.project_id == project_id,
            WorktreeModel.workspace_id == workspace_id,
            WorktreeModel.branch == branch,
            WorktreeModel.deleted_at.is_(None),
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def count_active(self, project_id: UUID, workspace_id: UUID) -> int:
        """Count non-deleted worktrees in a project."""
        query = (
            select(func.count())
            .select_from(WorktreeModel)
            .where(
                WorktreeModel.project_id == project_id,
                WorktreeModel.workspace_id == workspace_id,
                WorktreeModel.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def next_free_slot(self, project_id: UUID, workspace_id: UUID) -> int:
        """Return the lowest unused slot for a project's worktrees."""
        query = select(WorktreeModel.slot).where(
            WorktreeModel.project_id == project_id,
            WorktreeModel.workspace_id == workspace_id,
            WorktreeModel.deleted_at.is_(None),
        )
        result = await self._session.execute(query)
        used = {int(slot) for slot in result.scalars().all()}
        slot = 0
        while slot in used:
            slot += 1
        return slot
