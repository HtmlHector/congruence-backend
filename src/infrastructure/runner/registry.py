"""Runner Registry providing pluggable HostBackend instances."""

from __future__ import annotations

import os

from src.infrastructure.runner.base import HostBackend
from src.infrastructure.runner.fly import FlySpriteRunner
from src.infrastructure.runner.local import LocalProcessRunner

_runner_instance: HostBackend | None = None


def get_runner() -> HostBackend:
    """Return configured HostBackend runner."""
    global _runner_instance
    if _runner_instance is None:
        runner_type = os.getenv("CONGRUENCE_RUNNER_TYPE", "process_local")
        if runner_type == "fly_sprite":
            _runner_instance = FlySpriteRunner(
                api_token=os.getenv("FLY_API_TOKEN"),
                app_name=os.getenv("FLY_APP_NAME", "congruence-runners"),
            )
        else:
            _runner_instance = LocalProcessRunner()
    return _runner_instance
