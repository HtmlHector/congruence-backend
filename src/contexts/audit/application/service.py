"""Audit application service."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.audit.domain.entities import AuditLogEntry
from src.contexts.audit.infrastructure.repository import AuditLogRepository
from src.infrastructure.messaging.bus import message_bus
from src.infrastructure.messaging.topology import RK_AUDIT_LOG


class AuditService:
    """Manages append-only audit trail logging."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = AuditLogRepository(session)

    async def record(
        self,
        workspace_id: UUID,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Persist an audit log entry and publish to message bus."""
        entry = AuditLogEntry(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload or {},
        )
        saved = await self._repo.add(entry)

        # Broadcast via RabbitMQ
        await message_bus.publish(
            routing_key=RK_AUDIT_LOG,
            payload={
                "audit_id": str(saved.id),
                "workspace_id": str(workspace_id),
                "actor_id": actor_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "payload": payload or {},
            },
            workspace_id=str(workspace_id),
        )

        return saved

    async def list_for_workspace(
        self,
        workspace_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLogEntry]:
        """Fetch audit trail for a workspace."""
        entries = await self._repo.list_by_workspace(workspace_id, limit, offset)
        return list(entries)
