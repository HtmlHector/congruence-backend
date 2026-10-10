"""Audit log repository implementation using async SQLAlchemy."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.audit.domain.entities import AuditLogEntry
from src.contexts.audit.infrastructure.models import AuditLogModel
from src.shared.repository import AbstractRepository


class AuditLogRepository(AbstractRepository[AuditLogEntry]):
    """SQLAlchemy implementation of audit log persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, model: AuditLogModel) -> AuditLogEntry:
        return AuditLogEntry(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            actor_id=model.actor_id,
            action=model.action,
            resource_type=model.resource_type,
            resource_id=model.resource_id,
            payload=model.payload,
        )

    async def get_by_id(self, entity_id: UUID) -> AuditLogEntry | None:
        model = await self._session.get(AuditLogModel, entity_id)
        return self._to_entity(model) if model else None

    async def list_by_workspace(
        self,
        workspace_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[AuditLogEntry]:
        query = (
            select(AuditLogModel)
            .where(AuditLogModel.workspace_id == workspace_id)
            .order_by(desc(AuditLogModel.created_at))
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[AuditLogEntry]:
        query = (
            select(AuditLogModel)
            .order_by(desc(AuditLogModel.created_at))
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def add(self, entity: AuditLogEntry) -> AuditLogEntry:
        model = AuditLogModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            actor_id=entity.actor_id,
            action=entity.action,
            resource_type=entity.resource_type,
            resource_id=entity.resource_id,
            payload=entity.payload,
        )
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def update(self, entity: AuditLogEntry) -> AuditLogEntry:
        raise NotImplementedError("Audit logs are append-only and cannot be mutated")

    async def delete(self, entity_id: UUID) -> bool:
        raise NotImplementedError("Audit logs are append-only and cannot be deleted")
