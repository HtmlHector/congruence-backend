"""Usage record repository implementation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.usage.domain.entities import UsageRecord
from src.contexts.usage.infrastructure.models import UsageRecordModel
from src.shared.tenant_repository import TenantScopedRepository


class UsageRepository(TenantScopedRepository[UsageRecordModel, UsageRecord]):
    """SQLAlchemy persistence for metered usage."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    @property
    def _model_cls(self) -> type[UsageRecordModel]:
        return UsageRecordModel

    def _to_entity(self, model: UsageRecordModel) -> UsageRecord:
        return UsageRecord(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            project_id=model.project_id,
            devbox_id=model.devbox_id,
            kind=model.kind,
            quantity=model.quantity,
            period_start=model.period_start,
            period_end=model.period_end,
            reported_to_stripe_at=model.reported_to_stripe_at,
        )

    def _to_model(self, entity: UsageRecord) -> UsageRecordModel:
        return UsageRecordModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            project_id=entity.project_id,
            devbox_id=entity.devbox_id,
            kind=entity.kind,
            quantity=entity.quantity,
            period_start=entity.period_start,
            period_end=entity.period_end,
            reported_to_stripe_at=entity.reported_to_stripe_at,
        )

    async def total_seconds_since(self, workspace_id: UUID, since: datetime) -> int:
        """Sum metered awake seconds for a workspace since a timestamp."""
        query = select(func.coalesce(func.sum(UsageRecordModel.quantity), 0)).where(
            UsageRecordModel.workspace_id == workspace_id,
            UsageRecordModel.period_start >= since,
            UsageRecordModel.kind == "box_awake_seconds",
        )
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def list_for_workspace_since(
        self, workspace_id: UUID, since: datetime
    ) -> Sequence[UsageRecord]:
        """List usage records for reporting and UI breakdowns."""
        query = (
            select(UsageRecordModel)
            .where(
                UsageRecordModel.workspace_id == workspace_id,
                UsageRecordModel.period_start >= since,
            )
            .order_by(UsageRecordModel.period_start.desc())
        )
        result = await self._session.execute(query)
        return [self._to_entity(model) for model in result.scalars().all()]
