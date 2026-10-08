"""Abstract Base Interface for Pluggable Host Execution Engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class HostInfo:
    host_id: str
    project_id: str
    backend_type: str
    state: str
    provider_instance_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class LaneInfo:
    lane_id: str
    host_id: str
    branch_name: str
    worktree_path: str
    is_pair_lane: bool
    status: str = "ready"


@dataclass
class GitDiffInfo:
    lane_id: str
    branch_name: str
    files_changed: int
    insertions: int
    deletions: int
    diff_text: str
    raw_files: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DevServerInfo:
    lane_id: str
    port: int
    pid: int | None
    is_running: bool
    url: str


class HostBackend(ABC):
    """Abstract interface for microVM/container/process runner environments."""

    @abstractmethod
    async def create_host(
        self, project_id: str, size: str = "standard", repo_url: str | None = None
    ) -> HostInfo:
        """Provision a persistent runner instance."""
        ...

    @abstractmethod
    async def wake_host(self, host_id: str) -> HostInfo:
        """Wake a sleeping runner instance (<300ms target)."""
        ...

    @abstractmethod
    async def sleep_host(self, host_id: str) -> HostInfo:
        """Snapshot and sleep runner instance."""
        ...

    @abstractmethod
    async def get_host_state(self, host_id: str) -> str:
        """Return 'awake', 'asleep', 'waking', or 'sleeping'."""
        ...

    @abstractmethod
    async def create_lane(
        self, host_id: str, branch_name: str, is_pair_lane: bool = False
    ) -> LaneInfo:
        """Create an isolated Git worktree lane."""
        ...

    @abstractmethod
    async def spawn_pty(
        self,
        lane_id: str,
        command: list[str] | str,
        env: dict[str, str] | None = None,
        cols: int = 80,
        rows: int = 24,
    ) -> str:
        """Spawn an interactive PTY session for CLI agent or shell; returns pty_id."""
        ...

    @abstractmethod
    async def write_pty(self, pty_id: str, data: str | bytes) -> None:
        """Send input to running PTY session."""
        ...

    @abstractmethod
    async def resize_pty(self, pty_id: str, cols: int, rows: int) -> None:
        """Resize PTY window dimensions."""
        ...

    @abstractmethod
    async def read_pty_stream(self, pty_id: str) -> AsyncGenerator[str, None]:
        """Async generator streaming terminal stdout/stderr chunks."""
        ...

    @abstractmethod
    async def terminate_pty(self, pty_id: str) -> None:
        """Terminate active PTY session."""
        ...

    @abstractmethod
    async def start_dev_server(
        self, lane_id: str, command: str, port: int = 3000
    ) -> DevServerInfo:
        """Run application dev server in lane worktree."""
        ...

    @abstractmethod
    async def stop_dev_server(self, lane_id: str) -> None:
        """Stop application dev server in lane worktree."""
        ...

    @abstractmethod
    async def get_diff(self, lane_id: str) -> GitDiffInfo:
        """Get live git diff for lane worktree against base branch."""
        ...

    @abstractmethod
    async def list_lane_files(self, lane_id: str, subpath: str = "") -> dict[str, Any]:
        """List live hierarchical filesystem tree for the lane's worktree."""
        ...

    @abstractmethod
    async def read_lane_file(self, lane_id: str, file_path: str) -> str:
        """Read text content of a file in the lane's worktree."""
        ...

    @abstractmethod
    async def write_lane_file(self, lane_id: str, file_path: str, content: str) -> None:
        """Write text content to a file in the lane's worktree."""
        ...
