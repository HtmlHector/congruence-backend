"""Real-time event broadcasting helpers."""

from __future__ import annotations

from typing import Any

import structlog

from src.realtime.server import sio

logger = structlog.get_logger(__name__)


async def emit_to_workspace(workspace_id: str, event_name: str, payload: dict[str, Any]) -> None:
    """Broadcast an event to all connected sockets in a given workspace room."""
    room = f"workspace:{workspace_id}"
    try:
        await sio.emit(event_name, payload, room=room)
        logger.debug("realtime_event_emitted", room=room, event_type=event_name)
    except Exception as exc:
        logger.warning("realtime_emit_failed", room=room, event_type=event_name, error=str(exc))


async def emit_to_user(user_id: str, event_name: str, payload: dict[str, Any]) -> None:
    """Broadcast an event directly to a specific user socket room."""
    room = f"user:{user_id}"
    try:
        await sio.emit(event_name, payload, room=room)
        logger.debug("realtime_user_event_emitted", room=room, event_type=event_name)
    except Exception as exc:
        logger.warning(
            "realtime_user_emit_failed", room=room, event_type=event_name, error=str(exc)
        )
