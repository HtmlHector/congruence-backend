"""Ephemeral local TCP forwarder bridging browser preview traffic to devbox containers."""

from __future__ import annotations

import asyncio
import os
import socket
import structlog
from typing import Dict, Tuple

from src.config.settings import settings
from src.contexts.devbox.infrastructure.providers.local import docker_executable

logger = structlog.get_logger(__name__)

# Script executed inside container to bridge stdin/stdout with target port.
_CONTAINER_RELAY_SCRIPT = """
import socket, sys, select, os, fcntl

target_port = {port}
try:
    sock = socket.create_connection(("127.0.0.1", target_port), timeout=3.0)
except Exception:
    sys.exit(111)

sock.setblocking(False)
stdin_fd = sys.stdin.fileno()
stdout_fd = sys.stdout.fileno()
flags = fcntl.fcntl(stdin_fd, fcntl.F_GETFL)
fcntl.fcntl(stdin_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

read_list = [stdin_fd, sock]
while read_list:
    r, _, _ = select.select(read_list, [], [], 30.0)
    if not r:
        break
    if stdin_fd in r:
        try:
            data = os.read(stdin_fd, 65536)
            if not data:
                read_list.remove(stdin_fd)
                try:
                    sock.shutdown(socket.SHUT_WR)
                except Exception:
                    pass
            else:
                sock.sendall(data)
        except Exception:
            break
    if sock in r:
        try:
            data = sock.recv(65536)
            if not data:
                break
            os.write(stdout_fd, data)
        except Exception:
            break
"""


class DevboxPortBridge:
    """Manages an ephemeral local TCP listener that pipes traffic into a devbox port."""

    def __init__(self, box_id: str, target_port: int, provider: str = "local") -> None:
        self.box_id = box_id
        self.target_port = target_port
        self.provider = provider
        self._server: asyncio.Server | None = None
        self._local_port: int = 0
        self._active_tasks: set[asyncio.Task[None]] = set()

    @property
    def local_port(self) -> int:
        return self._local_port

    async def start(self) -> int:
        if self._server is not None:
            return self._local_port

        self._server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=0,
        )
        self._local_port = self._server.sockets[0].getsockname()[1]
        logger.info(
            "devbox_preview_bridge_started",
            box_id=self.box_id,
            target_port=self.target_port,
            local_port=self._local_port,
        )
        return self._local_port

    async def check_listening(self) -> bool:
        """Check if target_port is actively listening inside the box."""
        if self.provider == "memory":
            try:
                s = socket.create_connection(("127.0.0.1", self.target_port), timeout=0.3)
                s.close()
                return True
            except OSError:
                return False

        # Docker container check
        check_script = f"import socket; socket.create_connection(('127.0.0.1', {self.target_port}), timeout=0.5).close()"
        try:
            proc = await asyncio.create_subprocess_exec(
                docker_executable(),
                "exec",
                self.box_id,
                "python3",
                "-c",
                check_script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            code = await proc.wait()
            return code == 0
        except Exception as exc:
            logger.debug("check_listening_error", error=str(exc))
            return False

    async def _handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task:
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)

        if self.provider == "memory":
            try:
                remote_reader, remote_writer = await asyncio.open_connection(
                    "127.0.0.1", self.target_port
                )
            except OSError:
                client_writer.close()
                await client_writer.wait_closed()
                return

            await self._pipe_streams(client_reader, client_writer, remote_reader, remote_writer)
            return

        # Docker local provider
        relay_code = _CONTAINER_RELAY_SCRIPT.format(port=self.target_port)
        try:
            proc = await asyncio.create_subprocess_exec(
                docker_executable(),
                "exec",
                "-i",
                self.box_id,
                "python3",
                "-u",
                "-c",
                relay_code,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.error("failed_to_spawn_relay_process", error=str(exc))
            client_writer.close()
            await client_writer.wait_closed()
            return

        assert proc.stdin is not None
        assert proc.stdout is not None

        async def pipe_client_to_proc() -> None:
            try:
                while not client_reader.at_eof():
                    data = await client_reader.read(65536)
                    if not data:
                        break
                    proc.stdin.write(data)
                    await proc.stdin.drain()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            finally:
                try:
                    proc.stdin.close()
                    await proc.stdin.wait_closed()
                except Exception:
                    pass

        async def pipe_proc_to_client() -> None:
            try:
                while True:
                    data = await proc.stdout.read(65536)
                    if not data:
                        break
                    client_writer.write(data)
                    await client_writer.drain()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            finally:
                try:
                    client_writer.close()
                    await client_writer.wait_closed()
                except Exception:
                    pass

        await asyncio.gather(pipe_client_to_proc(), pipe_proc_to_client(), return_exceptions=True)
        try:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        except Exception:
            pass

    async def _pipe_streams(
        self,
        c_reader: asyncio.StreamReader,
        c_writer: asyncio.StreamWriter,
        r_reader: asyncio.StreamReader,
        r_writer: asyncio.StreamWriter,
    ) -> None:
        async def forward(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while not src.at_eof():
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                    await dst.wait_closed()
                except Exception:
                    pass

        await asyncio.gather(forward(c_reader, r_writer), forward(r_reader, c_writer))

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for t in list(self._active_tasks):
            t.cancel()


class ForwarderPool:
    """Registry of active DevboxPortBridge listeners."""

    def __init__(self) -> None:
        self._bridges: Dict[Tuple[str, int], DevboxPortBridge] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self, box_id: str, target_port: int, provider: str = "local"
    ) -> DevboxPortBridge:
        key = (box_id, target_port)
        async with self._lock:
            bridge = self._bridges.get(key)
            if bridge is None:
                bridge = DevboxPortBridge(box_id, target_port, provider)
                await bridge.start()
                self._bridges[key] = bridge
            return bridge

    async def close_all(self) -> None:
        async with self._lock:
            for bridge in self._bridges.values():
                await bridge.close()
            self._bridges.clear()


forwarder_pool = ForwarderPool()
