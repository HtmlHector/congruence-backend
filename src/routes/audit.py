"""Audit log query endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.auth.dependencies import require_permission
from src.infrastructure.database.session import get_db

router = APIRouter(prefix="/workspaces", tags=["audit"])


@router.get("/{workspace_id}/audit-logs")
async def list_audit_logs(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None),
    membership: dict = Depends(require_permission("workspace:audit_read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Retrieve audit log entries for a workspace."""
    query = """
        SELECT log_id, actor_id, actor_type, action, resource_type, resource_id, details, ip_address, created_at
        FROM audit.audit_log
        WHERE workspace_id = :ws_id
    """
    params: dict[str, Any] = {"ws_id": workspace_id, "limit": limit, "offset": offset}

    if action:
        query += " AND action = :action"
        params["action"] = action

    query += " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"

    result = await db.execute(text(query), params)
    return [
        {
            "log_id": str(row[0]),
            "actor_id": row[1],
            "actor_type": row[2],
            "action": row[3],
            "resource_type": row[4],
            "resource_id": row[5],
            "details": row[6],
            "ip_address": row[7],
            "created_at": row[8].isoformat() if row[8] else None,
        }
        for row in result.fetchall()
    ]
