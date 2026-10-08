"""Unified Real-time WebSocket Gateway for Workspace Sessions."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.infrastructure.runner.registry import get_runner

router = APIRouter(prefix="/ws", tags=["Session WebSocket"])


class SessionConnectionManager:
    """Manages active WebSocket connections, PTY relays, and lane subscriptions."""

    def __init__(self) -> None:
        # session_id -> set of active WebSockets
        self._connections: dict[str, set[WebSocket]] = {}
        # session_id -> { lane_id -> current_writer_id }
        self._lane_leases: dict[str, dict[str, str]] = {}
        # session_id -> active pty ids
        self._active_ptys: dict[str, set[str]] = {}

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        if session_id not in self._connections:
            self._connections[session_id] = set()
            self._lane_leases[session_id] = {}
            self._active_ptys[session_id] = set()
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


@router.websocket("/session/{session_id}")
async def session_websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """Unified bidirectional session gateway multiplexing PTY, lease control, and runner events."""
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

        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except Exception:
                continue

            msg_type = msg.get("type")

            # 1. PTY Spawning
            if msg_type == "spawn_pty":
                lane_id = msg.get("lane_id", "lane_main")
                command = msg.get("command", "/bin/sh")
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                env = msg.get("env", {})

                pty_id = await runner.spawn_pty(lane_id, command, env, cols, rows)
                manager._active_ptys[session_id].add(pty_id)

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
                            # Detect and rewrite localhost:PORT links to private preview URLs
                            rewritten = chunk
                            # Regex matches http://localhost:PORT or http://127.0.0.1:PORT
                            pattern = r"http://(?:localhost|127\.0\.0\.1):(\d{2,5})"
                            ports_found = re.findall(pattern, chunk)
                            if ports_found:
                                for p in ports_found:
                                    preview_target = f"/preview/{stream_lane_id}/{p}/ [HTTPS private]"
                                    rewritten = re.sub(
                                        rf"http://(?:localhost|127\.0\.0\.1):{p}",
                                        preview_target,
                                        rewritten,
                                    )
                                    # Also notify session of active detected dev server
                                    await manager.broadcast_to_session(
                                        session_id,
                                        {
                                            "type": "dev_server_status",
                                            "lane_id": stream_lane_id,
                                            "is_running": True,
                                            "port": int(p),
                                            "url": f"/preview/{stream_lane_id}/{p}/",
                                        },
                                    )

                            await manager.broadcast_to_session(
                                session_id,
                                {"type": "pty_output", "pty_id": p_id, "data": rewritten},
                            )
                    except Exception:
                        pass

                tasks.append(asyncio.create_task(_stream_pty(pty_id, lane_id)))

            # 2. PTY Input Write (Checked against lane write grant)
            elif msg_type == "pty_input":
                pty_id = msg.get("pty_id")
                data = msg.get("data", "")
                actor_id = msg.get("actor_id", "act_human")
                lane_id = msg.get("lane_id")
                
                # If a specific lane writer lease is established, ensure caller matches or is owner
                current_writer = None
                if lane_id and session_id in manager._lane_leases:
                    current_writer = manager._lane_leases[session_id].get(lane_id)

                if current_writer and actor_id != current_writer and actor_id != "act_human":
                    # Drop unauthorized stdin
                    await websocket.send_text(
                        json.dumps({
                            "type": "pty_input_denied",
                            "reason": f"Lane write lease held by {current_writer}",
                        })
                    )
                elif pty_id:
                    await runner.write_pty(pty_id, data)

            # 3. PTY Window Resize
            elif msg_type == "pty_resize":
                pty_id = msg.get("pty_id")
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                if pty_id:
                    await runner.resize_pty(pty_id, cols, rows)

            # 4. Lane Lease Control (Single-Writer Grant/Revoke)
            elif msg_type == "grant_control":
                lane_id = msg.get("lane_id")
                actor_id = msg.get("actor_id")
                if lane_id and actor_id:
                    manager._lane_leases[session_id][lane_id] = actor_id
                    await manager.broadcast_to_session(
                        session_id,
                        {
                            "type": "lease_updated",
                            "lane_id": lane_id,
                            "current_writer": actor_id,
                            "allow_watchers": True,
                        },
                    )

            elif msg_type == "revoke_control":
                lane_id = msg.get("lane_id")
                if lane_id:
                    manager._lane_leases[session_id][lane_id] = "act_human"
                    await manager.broadcast_to_session(
                        session_id,
                        {
                            "type": "lease_updated",
                            "lane_id": lane_id,
                            "current_writer": "act_human",
                            "allow_watchers": True,
                        },
                    )

            # 5. Dev Server Lifecycle
            elif msg_type == "start_dev":
                lane_id = msg.get("lane_id", "lane_main")
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
                lane_id = msg.get("lane_id", "lane_main")
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
                await runner.sleep_host(session_id)
                await manager.broadcast_to_session(
                    session_id,
                    {"type": "host_state_changed", "state": "asleep"},
                )

            elif msg_type == "wake_workspace":
                await runner.wake_host(session_id)
                await manager.broadcast_to_session(
                    session_id,
                    {"type": "host_state_changed", "state": "awake"},
                )

            # 7. Git Worktree Diff Query
            elif msg_type == "get_diff":
                lane_id = msg.get("lane_id", "lane_main")
                diff_info = await runner.get_diff(lane_id)
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "diff_result",
                            "lane_id": lane_id,
                            "files_changed": diff_info.files_changed,
                            "diff_text": diff_info.diff_text,
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
