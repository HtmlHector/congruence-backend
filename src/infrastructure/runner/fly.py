"""Fly runner capability boundary.

The configured Fly credentials are not enough to provision and operate the
persistent workspace contract yet. Every operation fails explicitly until a
real provider integration is implemented; this backend never reports synthetic
hosts, sessions, previews, or diffs as successful.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from src.infrastructure.runner.base import (
    DevServerInfo,
    GitDiffInfo,
    HostBackend,
    HostInfo,
    LaneInfo,
)


class FlySpriteNotImplementedError(RuntimeError):
    """The Fly runner does not yet implement real host operations."""


def _unsupported(operation: str) -> FlySpriteNotImplementedError:
    return FlySpriteNotImplementedError(
        f"Fly runner operation '{operation}' is not implemented; "
        "select CONGRUENCE_RUNNER_TYPE=docker or process_local for a working runner"
    )


class FlySpriteRunner(HostBackend):
    """Fail-closed Fly backend until durable Machines lifecycle is implemented."""

    def __init__(self, api_token: str | None = None, app_name: str = "congruence-runners") -> None:
        self.api_token = api_token
        self.app_name = app_name
        self.base_url = f"https://api.machines.dev/v1/apps/{app_name}"

    async def create_host(self, project_id: str, size: str = "standard", repo_url: str | None = None) -> HostInfo:
        raise _unsupported("create_host")

    async def wake_host(self, host_id: str) -> HostInfo:
        raise _unsupported("wake_host")

    async def sleep_host(self, host_id: str) -> HostInfo:
        raise _unsupported("sleep_host")

    async def get_host_state(self, host_id: str) -> str:
        raise _unsupported("get_host_state")

    async def create_lane(self, host_id: str, branch_name: str, is_pair_lane: bool = False) -> LaneInfo:
        raise _unsupported("create_lane")

    async def spawn_pty(
        self,
        lane_id: str,
        command: list[str] | str,
        env: dict[str, str] | None = None,
        cols: int = 80,
        rows: int = 24,
    ) -> str:
        raise _unsupported("spawn_pty")

    async def write_pty(self, pty_id: str, data: str | bytes) -> None:
        raise _unsupported("write_pty")

    async def resize_pty(self, pty_id: str, cols: int, rows: int) -> None:
        raise _unsupported("resize_pty")

    async def read_pty_stream(self, pty_id: str) -> AsyncGenerator[str, None]:
        raise _unsupported("read_pty_stream")
        yield ""  # Makes this an async generator; unreachable.

    async def terminate_pty(self, pty_id: str) -> None:
        raise _unsupported("terminate_pty")

    async def start_dev_server(self, lane_id: str, command: str, port: int = 3000) -> DevServerInfo:
        raise _unsupported("start_dev_server")

    async def stop_dev_server(self, lane_id: str) -> None:
        raise _unsupported("stop_dev_server")

    async def get_diff(self, lane_id: str) -> GitDiffInfo:
        raise _unsupported("get_diff")

    async def list_lane_files(self, lane_id: str, subpath: str = "") -> dict[str, Any]:
        raise _unsupported("list_lane_files")

    async def read_lane_file(self, lane_id: str, file_path: str) -> str:
        raise _unsupported("read_lane_file")

    async def write_lane_file(self, lane_id: str, file_path: str, content: str) -> None:
        raise _unsupported("write_lane_file")
