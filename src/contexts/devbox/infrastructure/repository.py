"""Devbox repository implementation."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.devbox.domain.entities import LIVE_DEVBOX_STATUSES, Devbox
from src.contexts.devbox.infrastructure.models import DevboxModel
from src.shared.tenant_repository import TenantScopedRepository


class DevboxRepository(TenantScopedRepository[DevboxModel, Devbox]):
    """SQLAlchemy persistence for devboxes scoped by workspace."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    @property
    def _model_cls(self) -> type[DevboxModel]:
        return DevboxModel

    def _to_entity(self, model: DevboxModel) -> Devbox:
        return Devbox(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            project_id=model.project_id,
            provider=model.provider,
            provider_box_id=model.provider_box_id,
            region=model.region,
            status=model.status,
            image_ref=model.image_ref,
            cpu=model.cpu,
            memory_mb=model.memory_mb,
            disk_gb=model.disk_gb,
            last_activity_at=model.last_activity_at,
            last_resumed_at=model.last_resumed_at,
            paused_at=model.paused_at,
            awake_seconds_total=model.awake_seconds_total,
            error_code=model.error_code,
        )

    def _to_model(self, entity: Devbox) -> DevboxModel:
        return DevboxModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            project_id=entity.project_id,
            provider=entity.provider,
            provider_box_id=entity.provider_box_id,
            region=entity.region,
            status=entity.status,
            image_ref=entity.image_ref,
            cpu=entity.cpu,
            memory_mb=entity.memory_mb,
            disk_gb=entity.disk_gb,
            last_activity_at=entity.last_activity_at,
            last_resumed_at=entity.last_resumed_at,
            paused_at=entity.paused_at,
            awake_seconds_total=entity.awake_seconds_total,
            error_code=entity.error_code,
        )

    async def get_live_for_project(self, project_id: UUID, workspace_id: UUID) -> Devbox | None:
        """Return the live devbox for a project, if one exists."""
        query = (
            select(DevboxModel)
            .where(
                DevboxModel.project_id == project_id,
                DevboxModel.workspace_id == workspace_id,
                DevboxModel.status.in_(tuple(LIVE_DEVBOX_STATUSES)),
            )
            .limit(1)
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_live_for_workspace(self, workspace_id: UUID) -> Sequence[Devbox]:
        """List every live box in a workspace."""
        query = select(DevboxModel).where(
            DevboxModel.workspace_id == workspace_id,
            DevboxModel.status.in_(tuple(LIVE_DEVBOX_STATUSES)),
        )
        result = await self._session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]
