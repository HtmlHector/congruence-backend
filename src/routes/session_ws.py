"""Unified Real-time WebSocket Gateway for Workspace Sessions."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.congruence.models import ActivityEvent, Actor, Grant, Host, Lane, Project
from src.infrastructure.auth.jwt import UserClaims, decode_jwt_token
from src.infrastructure.auth.tenancy import lane_access
from src.infrastructure.database.session import get_db
from src.infrastructure.runner.registry import get_runner

router = APIRouter(prefix="/ws", tags=["Session WebSocket"])


def _extract_ws_token(websocket: WebSocket) -> str | None:
    """Read a bearer token from the query string or Authorization header."""
    token = websocket.query_params.get("token")
    if token:
        return token
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


async def _ws_user_or_none(websocket: WebSocket) -> UserClaims | None:
    token = _extract_ws_token(websocket)
    if not token:
        return None
    try:
        return decode_jwt_token(token)
    except Exception:
        return None


class SessionConnectionManager:
    """Manages active WebSocket connections, PTY relays, and lane subscriptions."""

    def __init__(self) -> None:
        # session_id -> set of active WebSockets
        self._connections: dict[str, set[WebSocket]] = {}
        # Compatibility cache only; persisted congruence_grants are authoritative.
        self._lane_leases: dict[str, dict[str, str]] = {}
        # session_id -> active pty ids and their lane bindings (process-local PTYs are ephemeral)
        self._active_ptys: dict[str, set[str]] = {}
        self._pty_lanes: dict[str, dict[str, str]] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        if session_id not in self._connections:
            self._connections[session_id] = set()
            self._lane_leases[session_id] = {}
            self._active_ptys[session_id] = set()
            self._pty_lanes[session_id] = {}
        self._connections[session_id].add(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        if session_id in self._connections:
            self._connections[session_id].discard(websocket)
            if not self._connections[session_id]:
                self._connections.pop(session_id, None)

    async def broadcast_to_session(self, session_id: str, message: dict[str, Any]) -> None:
        sockets = self._connections.get(session_id, set())
        dead_sockets = set()
        for ws in sockets:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead_sockets.add(ws)
        for dead in dead_sockets:
            sockets.discard(dead)


manager = SessionConnectionManager()


@router.websocket("/session/{session_id:path}")
async def session_websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Unified bidirectional session gateway multiplexing PTY, lease control, and runner events.

    Tenancy: the connection must present a valid bearer token, and the
    session's lane (``sess_{lane_id}``) must resolve to a project the caller
    is a member of. Unknown lanes carry no tenant resource and only require
    authentication.
    """
    user = await _ws_user_or_none(websocket)
    if user is None:
        await websocket.close(code=4401, reason="Authentication required")
        return

    session_lane = session_id.removeprefix("sess_")
    token = _extract_ws_token(websocket)
    is_mock = bool(
        settings.auth.allow_dev_tokens
        and (not token or token.startswith(("mock_", "test_")) or user.auth_method == "mock")
    )

    if not is_mock:
        access = await lane_access(db, user, session_lane)
        if access is False:
            await websocket.close(code=4403, reason="Forbidden lane")
            return

    await manager.connect(session_id, websocket)
    runner = get_runner()
    tasks: list[asyncio.Task] = []

    try:
        # Send initial state handshake
        await websocket.send_text(
            json.dumps(
                {
                    "type": "handshake_ack",
                    "session_id": session_id,
                    "host_state": await runner.get_host_state(session_id),
                    "timestamp": asyncio.get_running_loop().time(),
                }
            )
        )

        # Start background port scanner to broadcast newly detected dev services
        async def _port_scanner_loop():
            known_ports: set[int] = set()
            while True:
                try:
                    await asyncio.sleep(2.0)
                    if hasattr(runner, "detect_lane_ports"):
                        current_ports = set(await runner.detect_lane_ports(session_lane))
                        new_ports = current_ports - known_ports
                        for p in new_ports:
                            await manager.broadcast_to_session(
                                session_id,
                                {
                                    "type": "service_discovered",
                                    "lane_id": session_lane,
                                    "port": p,
                                    "url": f"http://localhost:{p}",
                                    "service_name": f"Dev Server ({p})",
                                    "is_running": True,
                                },
                            )
                        known_ports = current_ports
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass

        scanner_task = asyncio.create_task(_port_scanner_loop())
        tasks.append(scanner_task)

        # 7. Git Worktree Diff Query
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except Exception:
                continue

            msg_type = msg.get("type")
            msg_lane = msg.get("lane_id") or session_lane
            if is_mock:
                writes_allowed = True
            else:
                lane_ok = await lane_access(db, user, msg_lane)
                if lane_ok is False:
                    await websocket.send_text(json.dumps({"type": "error", "detail": "Lane not found or access denied"}))
                    continue

            # Resolve the caller's actor from persisted project membership;
            # the client-supplied actor_id is never used as an identity.
            actor_query = (
                select(Actor)
                .join(Project, Actor.project_id == Project.id)
                .join(Host, Host.project_id == Project.id)
                .join(Lane, Lane.host_id == Host.id)
                .where(
                    Lane.id == msg_lane,
                    Actor.user_id == user.user_id,
                    Actor.actor_type == "human",
                )
            )
            lane_actor = (await db.execute(actor_query)).scalars().first()
            if lane_actor is None and is_mock:
                lane_actor = (
                    await db.execute(
                        select(Actor)
                        .join(Lane, Lane.id == msg_lane)
                        .join(Host, Host.id == Lane.host_id)
                        .where(Actor.project_id == Host.project_id, Actor.role == "owner")
                    )
                ).scalars().first()

            writer_grant = (
                await db.execute(
                    select(Grant)
                    .where(Grant.lane_id == msg_lane, Grant.is_revoked.is_(False), Grant.permission == "write")
                    .order_by(Grant.granted_at.desc())
                )
            ).scalars().first()
            current_writer_id = writer_grant.actor_id if writer_grant else (lane_actor.id if lane_actor else None)
            writes_allowed = (
                is_mock
                or writer_grant is None
                or (lane_actor is not None and lane_actor.id == current_writer_id)
            )
            write_messages = {
                "spawn_pty", "pty_input", "pty_resize", "start_dev", "stop_dev",
                "sleep_workspace", "wake_workspace", "merge_lane", "grant_control", "revoke_control",
            }
            owner_only_messages = {"sleep_workspace", "wake_workspace", "grant_control", "revoke_control"}
            if msg_type in write_messages and not (
                is_mock
                or writes_allowed
                or (
                    lane_actor is not None
                    and (lane_actor.role == "owner" if msg_type in owner_only_messages else writes_allowed)
                )
            ):
                await websocket.send_text(json.dumps({"type": "error", "detail": "Write control required for this lane"}))
                continue

            # 1. PTY Spawning
            if msg_type == "spawn_pty":
                lane_id = msg_lane
                command = msg.get("command", "/bin/sh")
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                env = msg.get("env", {})

                pty_id = await runner.spawn_pty(lane_id, command, env, cols, rows)
                manager._active_ptys[session_id].add(pty_id)
                manager._pty_lanes.setdefault(session_id, {})[pty_id] = lane_id

                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "pty_spawned",
                            "pty_id": pty_id,
                            "lane_id": lane_id,
                        }
                    )
                )

                # Stream stdout/stderr task with localhost rewrite and service detection
                import re

                async def _stream_pty(p_id: str, stream_lane_id: str):
                    try:
                        async for chunk in runner.read_pty_stream(p_id):
                            rewritten = chunk
                            pattern = r"http://(?:localhost|127\.0\.0\.1):(\d{2,5})"
                            ports_found = re.findall(pattern, chunk)
                            if ports_found:
                                for p in ports_found:
                                    try:
                                        preview_target = await runner.get_preview_target(stream_lane_id, int(p), "")
                                    except Exception:
                                        preview_target = f"/preview/{stream_lane_id}/{p}/"
                                    rewritten = re.sub(
                                        rf"http://(?:localhost|127\.0\.0\.1):{p}",
                                        preview_target,
                                        rewritten,
                                    )
                                    await manager.broadcast_to_session(
                                        session_id,
                                        {
                                            "type": "service_discovered",
                                            "lane_id": stream_lane_id,
                                            "is_running": True,
                                            "port": int(p),
                                            "url": f"/preview/{stream_lane_id}/{p}/",
                                            "service_name": f"App ({p})",
                                        },
                                    )

                            await manager.broadcast_to_session(
                                session_id,
                                {"type": "pty_output", "pty_id": p_id, "data": rewritten},
                            )
                    except Exception:
                        pass

                tasks.append(asyncio.create_task(_stream_pty(pty_id, lane_id)))

            # 2. PTY Input Write
            elif msg_type == "pty_input":
                pty_id = msg.get("pty_id")
                data = msg.get("data", "")
                claimed_actor = msg.get("actor_id")
                if claimed_actor and lane_actor and claimed_actor != lane_actor.id:
                    await websocket.send_text(json.dumps({"type": "pty_input_denied", "reason": "Actor spoofing denied"}))
                elif not writes_allowed:
                    await websocket.send_text(json.dumps({"type": "pty_input_denied", "reason": "Lane write lease belongs to another actor"}))
                elif pty_id and pty_id in manager._active_ptys.get(session_id, set()):
                    await runner.write_pty(pty_id, data)
                else:
                    await websocket.send_text(json.dumps({"type": "pty_input_denied", "reason": "PTY does not belong to this session"}))

            # 3. PTY Window Resize
            elif msg_type == "pty_resize":
                pty_id = msg.get("pty_id")
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                if (
                    writes_allowed
                    and pty_id
                    and pty_id in manager._active_ptys.get(session_id, set())
                ):
                    await runner.resize_pty(pty_id, cols, rows)
                else:
                    await websocket.send_text(json.dumps({"type": "pty_input_denied", "reason": "Lane write lease required"}))

            # 4. Lane Lease Control
            elif msg_type == "grant_control":
                lane_id = msg_lane
                actor_id = msg.get("actor_id")
                if lane_actor and lane_actor.role == "owner" and actor_id:
                    target_actor = (
                        await db.execute(select(Actor).where(Actor.id == actor_id, Actor.project_id == lane_actor.project_id))
                    ).scalar_one_or_none()
                    if target_actor is None:
                        await websocket.send_text(json.dumps({"type": "error", "detail": "Actor not found in project"}))
                        continue
                    active = (await db.execute(select(Grant).where(Grant.lane_id == lane_id, Grant.is_revoked.is_(False)))).scalars().all()
                    for grant in active:
                        grant.is_revoked = True
                    grant = Grant(lane_id=lane_id, actor_id=target_actor.id, permission="write", granted_by_actor_id=lane_actor.id)
                    db.add(grant)
                    db.add(ActivityEvent(project_id=lane_actor.project_id, lane_id=lane_id, actor_id=lane_actor.id, action_type="grant_issued", summary=f"Write control granted to {target_actor.display_name}"))
                    await db.commit()
                    await manager.broadcast_to_session(session_id, {"type": "lease_updated", "lane_id": lane_id, "current_writer": target_actor.id, "allow_watchers": True})
                else:
                    await websocket.send_text(json.dumps({"type": "error", "detail": "Only a project owner may grant lane control"}))

            elif msg_type == "revoke_control":
                lane_id = msg_lane
                if lane_actor and lane_actor.role == "owner":
                    active = (await db.execute(select(Grant).where(Grant.lane_id == lane_id, Grant.is_revoked.is_(False)))).scalars().all()
                    for grant in active:
                        grant.is_revoked = True
                    db.add(ActivityEvent(project_id=lane_actor.project_id, lane_id=lane_id, actor_id=lane_actor.id, action_type="grant_revoked", summary="Lane write control reclaimed by owner"))
                    await db.commit()
                    await manager.broadcast_to_session(session_id, {"type": "lease_updated", "lane_id": lane_id, "current_writer": lane_actor.id, "allow_watchers": True})
                else:
                    await websocket.send_text(json.dumps({"type": "error", "detail": "Only a project owner may revoke lane control"}))

            # 5. Dev Server Lifecycle
            elif msg_type == "start_dev":
                lane_id = msg_lane
                command = msg.get("command", "echo 'dev server started' && sleep 3600")
                port = msg.get("port", 3000)
                srv = await runner.start_dev_server(lane_id, command, port)
                await manager.broadcast_to_session(
                    session_id,
                    {
                        "type": "dev_server_status",
                        "lane_id": lane_id,
                        "is_running": srv.is_running,
                        "port": srv.port,
                        "url": srv.url,
                    },
                )

            elif msg_type == "stop_dev":
                lane_id = msg_lane
                await runner.stop_dev_server(lane_id)
                await manager.broadcast_to_session(
                    session_id,
                    {
                        "type": "dev_server_status",
                        "lane_id": lane_id,
                        "is_running": False,
                    },
                )

            # 6. Sleep / Wake Host Transitions
            elif msg_type == "sleep_workspace":
                lane = (await db.execute(select(Lane).where(Lane.id == msg_lane))).scalar_one_or_none()
                host_id = lane.host_id if lane else None
                if host_id is None:
                    await websocket.send_text(json.dumps({"type": "error", "detail": "Host not found for lane"}))
                    continue
                await runner.sleep_host(host_id)
                await manager.broadcast_to_session(
                    session_id,
                    {"type": "host_state_changed", "state": "asleep"},
                )

            elif msg_type == "wake_workspace":
                lane = (await db.execute(select(Lane).where(Lane.id == msg_lane))).scalar_one_or_none()
                host_id = lane.host_id if lane else None
                if host_id is None:
                    await websocket.send_text(json.dumps({"type": "error", "detail": "Host not found for lane"}))
                    continue
                await runner.wake_host(host_id)
                await manager.broadcast_to_session(
                    session_id,
                    {"type": "host_state_changed", "state": "awake"},
                )

            # 7. Git Worktree Diff Query
            elif msg_type == "get_diff":
                lane_id = msg_lane
                diff_info = await runner.get_diff(lane_id)
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "diff_result",
                            "lane_id": lane_id,
                            "files_changed": diff_info.files_changed,
                            "insertions": diff_info.insertions,
                            "deletions": diff_info.deletions,
                            "diff_text": diff_info.diff_text,
                            "raw_files": getattr(diff_info, "raw_files", []),
                        }
                    )
                )

            # 8. Lane Merge Query
            elif msg_type == "merge_lane":
                lane_id = msg_lane
                target = msg.get("target_branch", "main")
                merge_res = await runner.merge_lane(lane_id, target)
                await manager.broadcast_to_session(
                    session_id,
                    {
                        "type": "merge_result",
                        "lane_id": lane_id,
                        "result": merge_res,
                    }
                )

            # 9. Screenshot / DOM Capture Query
            elif msg_type == "capture_screenshot":
                lane_id = msg_lane
                port = msg.get("port", 3001)
                if not hasattr(runner, "capture_lane_screenshot"):
                    await websocket.send_text(json.dumps({"type": "error", "detail": "Screenshot capture is unsupported by this runner"}))
                    continue
                snap = await runner.capture_lane_screenshot(lane_id, port)
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "screenshot_result",
                            "snapshot": snap,
                        }
                    )
                )

    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)
        for task in tasks:
            task.cancel()
    except Exception:
        manager.disconnect(session_id, websocket)
        for task in tasks:
            task.cancel()
    finally:
        if session_id not in manager._connections:
            active_ptys = manager._active_ptys.pop(session_id, set())
            manager._lane_leases.pop(session_id, None)
            manager._pty_lanes.pop(session_id, None)
            for pty_id in active_ptys:
                try:
                    await runner.terminate_pty(pty_id)
                except Exception:
                    pass
