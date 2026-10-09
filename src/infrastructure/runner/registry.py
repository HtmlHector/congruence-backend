"""Runner Registry providing pluggable HostBackend instances."""

from __future__ import annotations

import os
import subprocess

from src.infrastructure.runner.base import HostBackend
from src.infrastructure.runner.docker import DockerHostRunner
from src.infrastructure.runner.fly import FlySpriteRunner
from src.infrastructure.runner.local import LocalProcessRunner

_runner_instance: HostBackend | None = None


def _check_docker_available() -> bool:
    """Synchronous check if docker daemon is running and responsive."""
    try:
        res = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
        )
        return res.returncode == 0
    except Exception:
        return False


def get_runner() -> HostBackend:
    """Return configured HostBackend runner."""
    global _runner_instance
    if _runner_instance is None:
        runner_type = os.getenv("CONGRUENCE_RUNNER_TYPE", "auto").lower()
        if runner_type == "fly_sprite":
            _runner_instance = FlySpriteRunner(
                api_token=os.getenv("FLY_API_TOKEN"),
                app_name=os.getenv("FLY_APP_NAME", "congruence-runners"),
            )
        elif runner_type in ("docker", "process_docker"):
            _runner_instance = DockerHostRunner()
        elif runner_type == "process_local":
            _runner_instance = LocalProcessRunner()
        else:
            # Auto-detect: if docker daemon is accessible, use DockerHostRunner; otherwise fallback to LocalProcessRunner
            if _check_docker_available():
                _runner_instance = DockerHostRunner()
            else:
                _runner_instance = LocalProcessRunner()
    return _runner_instance

