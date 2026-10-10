"""Socket.IO real-time server with Redis manager for multi-instance scaling."""

from __future__ import annotations

from typing import Any

import socketio
import structlog

from src.config.settings import settings
from src.infrastructure.auth.clerk import clerk_service

logger = structlog.get_logger(__name__)

# Redis adapter for scaling Socket.IO across multiple API/Worker processes
mgr = None
if settings.redis.url:
    try:
        mgr = socketio.AsyncRedisManager(settings.redis.url)
    except Exception as exc:
        logger.warning("socketio_redis_manager_failed", error=str(exc))

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.app.cors_origins,
    client_manager=mgr,
    logger=False,
    engineio_logger=False,
)


@sio.event
async def connect(sid: str, _environ: dict, auth: dict | None = None) -> bool:
    """Authorize incoming Socket.IO connection via token."""
    token = auth.get("token") if auth else None
    if not token:
        # Fallback to query string or dev environment
        if settings.app.environment == "development":
            user_id = "dev_user_123"
            workspace_id = (
                auth.get("workspace_id") if auth else "00000000-0000-0000-0000-000000000001"
            )
            await sio.save_session(sid, {"user_id": user_id, "workspace_id": workspace_id})
            if workspace_id:
                await sio.enter_room(sid, f"workspace:{workspace_id}")
            logger.info(
                "socketio_connected_dev", sid=sid, user_id=user_id, workspace_id=workspace_id
            )
            return True
        logger.warning("socketio_connect_rejected_no_token", sid=sid)
        return False

    try:
        claims = await clerk_service.verify_token(token)
        workspace_id = claims.org_id
        await sio.save_session(sid, {"user_id": claims.user_id, "workspace_id": workspace_id})
        await sio.enter_room(sid, f"user:{claims.user_id}")
        if workspace_id:
            await sio.enter_room(sid, f"workspace:{workspace_id}")
        logger.info(
            "socketio_connected", sid=sid, user_id=claims.user_id, workspace_id=workspace_id
        )
        return True
    except Exception as exc:
        logger.warning("socketio_connect_rejected_invalid_token", sid=sid, error=str(exc))
        return False


@sio.event
async def disconnect(sid: str) -> None:
    """Clean up on client disconnect."""
    logger.info("socketio_disconnected", sid=sid)


@sio.event
async def join_workspace(sid: str, data: dict[str, Any]) -> None:
    """Client explicit workspace room subscription."""
    workspace_id = data.get("workspace_id")
    if workspace_id:
        await sio.enter_room(sid, f"workspace:{workspace_id}")
        logger.debug("socketio_room_joined", sid=sid, room=f"workspace:{workspace_id}")
