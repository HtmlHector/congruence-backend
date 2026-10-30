"""Local Docker devbox provider for development and single-node testing."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from src.config.settings import settings
from src.contexts.devbox.infrastructure.providers.base import (
    BoxSpec,
    ProviderBox,
    ProviderError,
)

_STATE_MAP = {
    "running": "running",
    "paused": "paused",
    "created": "starting",
    "restarting": "starting",
    "exited": "stopped",
    "dead": "error",
}

_DESKTOP_DOCKER = Path("/Applications/Docker.app/Contents/Resources/bin/docker")


def docker_executable() -> str:
    """Return the docker CLI to invoke.

    The API process often runs without Docker Desktop's bin directory on PATH, so
    fall back to the macOS app bundle before giving up.
    """
    configured = settings.devbox.docker_bin
    if configured:
        return configured
    found = shutil.which("docker")
    if found:
        return found
    if _DESKTOP_DOCKER.exists():
        return str(_DESKTOP_DOCKER)
    return "docker"


def workspace_volume(box_id: str) -> str:
    """Return the named volume backing a box's /workspaces directory."""
    return f"{box_id}-workspaces"


async def _docker(*args: str, timeout: float = 60.0) -> tuple[int, str, str]:
    """Run a docker CLI command and capture its output."""
    try:
        process = await asyncio.create_subprocess_exec(
            docker_executable(),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ProviderError("docker CLI is not installed") from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ProviderError(f"docker command timed out: {' '.join(args)}") from exc

    return (
        process.returncode or 0,
        stdout.decode("utf-8", "replace").strip(),
        stderr.decode("utf-8", "replace").strip(),
    )


class LocalDockerProvider:
    """Boots one container per project on the local Docker daemon."""

    name = "local"

    async def create(self, spec: BoxSpec) -> ProviderBox:
        code, out, err = await _docker(
            "run",
            "-d",
            "--name",
            spec.name,
            "--label",
            "congruence.devbox=true",
            "-v",
            f"{workspace_volume(spec.name)}:/workspaces",
            "-w",
            "/workspaces",
            *[item for key, value in spec.env.items() for item in ("-e", f"{key}={value}")],
            spec.image_ref,
        )
        if code != 0:
            if "No such image" in (err or out) or "pull access denied" in (err or out):
                raise ProviderError(
                    f"devbox image {spec.image_ref} is unavailable; "
                    "build it with 'make devbox-image'"
                )
            raise ProviderError(f"docker run failed: {err or out}")
        return ProviderBox(box_id=spec.name, provider=self.name, status="running")

    async def resume(self, box_id: str) -> ProviderBox:
        code, out, err = await _docker("unpause", box_id)
        if code != 0:
            code, out, err = await _docker("start", box_id)
        if code != 0:
            raise ProviderError(f"docker resume failed: {err or out}")
        return ProviderBox(box_id=box_id, provider=self.name, status="running")

    async def pause(self, box_id: str) -> None:
        code, out, err = await _docker("pause", box_id)
        if code != 0:
            raise ProviderError(f"docker pause failed: {err or out}")

    async def destroy(self, box_id: str) -> None:
        code, out, err = await _docker("rm", "-f", box_id)
        if code != 0 and "No such container" not in (err or out):
            raise ProviderError(f"docker rm failed: {err or out}")
        await _docker("volume", "rm", "-f", workspace_volume(box_id))

    async def status(self, box_id: str) -> str:
        code, out, _ = await _docker("inspect", "--format", "{{.State.Status}}", box_id)
        if code != 0:
            return "unknown"
        return _STATE_MAP.get(out, out or "unknown")
