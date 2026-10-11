"""Fail-closed behavior tests for the not-yet-implemented Fly backend."""

import pytest

from src.infrastructure.runner.fly import FlySpriteNotImplementedError, FlySpriteRunner


@pytest.mark.asyncio
async def test_fly_runner_does_not_report_fake_host_creation():
    runner = FlySpriteRunner(api_token=None)

    with pytest.raises(FlySpriteNotImplementedError, match="create_host.*not implemented"):
        await runner.create_host("proj_test")


@pytest.mark.asyncio
async def test_fly_runner_does_not_report_fake_pty_or_preview():
    runner = FlySpriteRunner(api_token=None)

    with pytest.raises(FlySpriteNotImplementedError, match="spawn_pty.*not implemented"):
        await runner.spawn_pty("lane_test", "/bin/sh")
    with pytest.raises(FlySpriteNotImplementedError, match="start_dev_server.*not implemented"):
        await runner.start_dev_server("lane_test", "npm run dev")
