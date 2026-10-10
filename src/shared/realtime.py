"""Realtime workspace event broadcasting primitive."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.realtime.events import emit_to_user, emit_to_workspace


async def broadcast_workspace_event(
    workspace_id: UUID | str,
    event_name: str,
    payload: dict[str, Any],
) -> None:
    """Convenience primitive to broadcast an event to a workspace's realtime room.

    Works from any application service, route handler, or background worker.
    """
    ws_id_str = str(workspace_id)
    await emit_to_workspace(workspace_id=ws_id_str, event_name=event_name, payload=payload)


async def broadcast_user_event(
    user_id: str,
    event_name: str,
    payload: dict[str, Any],
) -> None:
    """Convenience primitive to broadcast an event directly to a user's realtime room."""
    await emit_to_user(user_id=user_id, event_name=event_name, payload=payload)
