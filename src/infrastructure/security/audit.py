"""Audit logging service — records resource mutations for compliance.

Inserts directly to audit.audit_log using a SAVEPOINT (begin_nested) so failures never
abort or roll back the calling business transaction.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class AuditLogger:
    """Records audit trail entries for security-relevant resource mutations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        *,
        workspace_id: str,
        actor_id: str | None,
        actor_type: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Record an audit log entry.

        Args:
            workspace_id: Workspace where the action occurred.
            actor_id: User ID or system identifier of the actor.
            actor_type: 'USER', 'API_KEY', 'SYSTEM', or 'WEBHOOK'.
            action: Action string (e.g. 'workspace.created', 'member.invited').
            resource_type: Resource category (e.g. 'workspace', 'membership', 'billing').
            resource_id: ID of the affected resource, or None.
            details: JSON-serializable dict of changes and context.
            ip_address: Client IP address.
            user_agent: Client User-Agent string.
        """
        try:
            async with self._session.begin_nested():
                await self._session.execute(
                    text(
                        """
                        INSERT INTO audit.audit_log
                        (workspace_id, actor_id, actor_type, action, resource_type,
                         resource_id, details, ip_address, user_agent)
                        VALUES
                        (:workspace_id, :actor_id, :actor_type, :action, :resource_type,
                         :resource_id, :details, :ip_address, :user_agent)
                        """
                    ),
                    {
                        "workspace_id": workspace_id,
                        "actor_id": actor_id,
                        "actor_type": actor_type,
                        "action": action,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "details": json.dumps(details) if details is not None else "{}",
                        "ip_address": ip_address,
                        "user_agent": user_agent,
                    },
                )
        except Exception:
            # Audit failures must not fail the caller's operation
            logger.exception(
                "audit_log_write_failed",
                action=action,
                resource_type=resource_type,
                workspace_id=workspace_id,
            )
