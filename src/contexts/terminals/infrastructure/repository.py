"""Terminal repository implementation."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.terminals.domain.entities import Terminal, TerminalStatus
from src.contexts.terminals.infrastructure.models import TerminalModel
from src.contexts.worktrees.infrastructure.models import WorktreeModel
from src.shared.tenant_repository import TenantScopedRepository

_ACTIVE_STATUSES = (
    TerminalStatus.STARTING,
    TerminalStatus.OPEN,
    TerminalStatus.DETACHED,
    TerminalStatus.LOST,
)


class TerminalRepository(TenantScopedRepository[TerminalModel, Terminal]):
    """SQLAlchemy persistence for terminal sessions."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    @property
    def _model_cls(self) -> type[TerminalModel]:
        return TerminalModel

    def _to_entity(self, model: TerminalModel) -> Terminal:
        return Terminal(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            worktree_id=model.worktree_id,
            devbox_id=model.devbox_id,
            tmux_session=model.tmux_session,
            kind=model.kind,
            agent=model.agent,
            title=model.title,
            status=model.status,
            created_by=model.created_by,
            credential_id=model.credential_id,
            recording_enabled=model.recording_enabled,
            cols=model.cols,
            rows=model.rows,
            last_seq=model.last_seq,
            scrollback_bytes=model.scrollback_bytes,
            last_attached_at=model.last_attached_at,
            closed_at=model.closed_at,
        )

    def _to_model(self, entity: Terminal) -> TerminalModel:
        return TerminalModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            worktree_id=entity.worktree_id,
            devbox_id=entity.devbox_id,
            tmux_session=entity.tmux_session,
            kind=entity.kind,
            agent=entity.agent,
            title=entity.title,
            status=entity.status,
            created_by=entity.created_by,
            credential_id=entity.credential_id,
            recording_enabled=entity.recording_enabled,
            cols=entity.cols,
            rows=entity.rows,
            last_seq=entity.last_seq,
            scrollback_bytes=entity.scrollback_bytes,
            last_attached_at=entity.last_attached_at,
            closed_at=entity.closed_at,
        )

    async def list_for_worktree(
        self, worktree_id: UUID, workspace_id: UUID, *, include_closed: bool = False
    ) -> Sequence[Terminal]:
        """List terminals attached to a worktree."""
        query = select(TerminalModel).where(
            TerminalModel.worktree_id == worktree_id,
            TerminalModel.workspace_id == workspace_id,
        )
        if not include_closed:
            query = query.where(TerminalModel.status != TerminalStatus.CLOSED)
        query = query.order_by(TerminalModel.created_at.asc())
        result = await self._session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]

    async def count_active_for_worktree(self, worktree_id: UUID, workspace_id: UUID) -> int:
        """Count active terminals in a worktree."""
        query = (
            select(func.count())
            .select_from(TerminalModel)
            .where(
                TerminalModel.worktree_id == worktree_id,
                TerminalModel.workspace_id == workspace_id,
                TerminalModel.status.in_(_ACTIVE_STATUSES),
            )
        )
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def count_active_for_project(self, project_id: UUID, workspace_id: UUID) -> int:
        """Count active terminals across all worktrees of a project."""
        query = (
            select(func.count())
            .select_from(TerminalModel)
            .join(WorktreeModel, WorktreeModel.id == TerminalModel.worktree_id)
            .where(
                WorktreeModel.project_id == project_id,
                TerminalModel.workspace_id == workspace_id,
                TerminalModel.status.in_(_ACTIVE_STATUSES),
            )
        )
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def close_for_worktree(self, worktree_id: UUID, workspace_id: UUID) -> None:
        """Close every active terminal in a worktree."""
        query = select(TerminalModel).where(
            TerminalModel.worktree_id == worktree_id,
            TerminalModel.workspace_id == workspace_id,
            TerminalModel.status.in_(_ACTIVE_STATUSES),
        )
        result = await self._session.execute(query)
        for model in result.scalars().all():
            model.status = TerminalStatus.CLOSED
        await self._session.flush()
