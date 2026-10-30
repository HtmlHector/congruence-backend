"""Command execution inside a devbox.

In production these calls are served by the in-box agent over its outbound
WebSocket, so the control plane never needs inbound connectivity. The runner
protocol exists now so worktree/setup code does not depend on a specific
transport; the agent-backed implementation lands with the gateway milestone.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from src.config.settings import settings
from src.contexts.devbox.domain.entities import Devbox, DevboxStatus
from src.contexts.devbox.infrastructure.providers.base import (
    ProviderError,
    ProviderNotConfigured,
)
from src.contexts.devbox.infrastructure.providers.local import docker_executable


class BoxNotAwakeError(Exception):
    """Raised when a command is issued against a box that is not running."""


class BoxCommandError(Exception):
    """Raised when a box command exits non-zero."""

    def __init__(self, message: str, exit_code: int, stderr: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


@dataclass(frozen=True)
class ExecResult:
    """Captured result of a non-interactive box command."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class BoxCommandRunner(Protocol):
    """Runs a non-interactive command inside a devbox."""

    async def exec(
        self,
        box: Devbox,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecResult:
        """Execute a command and capture its output."""


class MemoryRunner:
    """Records commands without executing them, for deterministic tests."""

    commands: dict[str, list[list[str]]] = {}

    async def exec(
        self,
        box: Devbox,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecResult:
        _ = (cwd, env, timeout)
        self.commands.setdefault(str(box.id), []).append([*command])
        return ExecResult(exit_code=0, stdout="", stderr="")


class LocalDockerRunner:
    """Executes commands with ``docker exec`` inside a local devbox container."""

    async def exec(
        self,
        box: Devbox,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecResult:
        if box.status is not DevboxStatus.AWAKE:
            raise BoxNotAwakeError(f"Devbox {box.id} is {box.status}")
        if not box.provider_box_id:
            raise ProviderError("Devbox has no provider box id")

        args = [docker_executable(), "exec"]
        if cwd:
            args += ["-w", cwd]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        args += [box.provider_box_id, *command]

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout or settings.devbox.exec_timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise BoxCommandError("Command timed out", -1, "") from exc

        return ExecResult(
            exit_code=process.returncode or 0,
            stdout=stdout.decode("utf-8", "replace"),
            stderr=stderr.decode("utf-8", "replace"),
        )


def get_runner(provider_name: str) -> BoxCommandRunner:
    """Return the command runner matching the provider backend."""
    if provider_name == "memory":
        return MemoryRunner()
    if provider_name == "local":
        return LocalDockerRunner()
    raise ProviderNotConfigured(
        f"Command transport for provider '{provider_name}' ships with the box-agent "
        "gateway milestone; use the local or memory provider for now."
    )
