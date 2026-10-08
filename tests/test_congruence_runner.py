"""Tests for LocalProcessRunner and PTY engine."""

import pytest

from src.infrastructure.runner.local import LocalProcessRunner


@pytest.mark.asyncio
async def test_runner_host_lifecycle():
    runner = LocalProcessRunner()
    
    # Create host
    host = await runner.create_host("proj_test_123")
    assert host.host_id.startswith("host_")
    assert host.state == "awake"

    # Sleep host
    slept = await runner.sleep_host(host.host_id)
    assert slept.state == "asleep"
    assert await runner.get_host_state(host.host_id) == "asleep"

    # Wake host
    woken = await runner.wake_host(host.host_id)
    assert woken.state == "awake"
    assert await runner.get_host_state(host.host_id) == "awake"


@pytest.mark.asyncio
async def test_runner_lane_creation():
    runner = LocalProcessRunner()
    host = await runner.create_host("proj_test_456")

    lane = await runner.create_lane(host.host_id, "agent/fix-auth", is_pair_lane=False)
    assert lane.lane_id.startswith("lane_")
    assert lane.branch_name == "agent/fix-auth"
    assert lane.is_pair_lane is False


@pytest.mark.asyncio
async def test_runner_pty_interactive_stream():
    runner = LocalProcessRunner()
    host = await runner.create_host("proj_test_789")
    lane = await runner.create_lane(host.host_id, "main", is_pair_lane=True)

    # Spawn PTY running echo command
    pty_id = await runner.spawn_pty(lane.lane_id, ["/bin/sh", "-c", "echo 'CONGRUENCE_PTY_OK'"])
    assert pty_id.startswith("pty_")

    output = ""
    async for chunk in runner.read_pty_stream(pty_id):
        output += chunk
        if "CONGRUENCE_PTY_OK" in output:
            break

    assert "CONGRUENCE_PTY_OK" in output

    await runner.terminate_pty(pty_id)


@pytest.mark.asyncio
async def test_runner_pty_sandbox_jail_home():
    runner = LocalProcessRunner()
    host = await runner.create_host("proj_test_jail")
    lane = await runner.create_lane(host.host_id, "main", is_pair_lane=True)
    cwd = await runner.resolve_lane_cwd(lane.lane_id)

    # Spawn PTY running echo of HOME and CONGRUENCE_WORKTREE_ROOT
    pty_id = await runner.spawn_pty(
        lane.lane_id,
        ["/bin/sh", "-c", "echo \"JAIL_HOME=$HOME JAIL_ROOT=$CONGRUENCE_WORKTREE_ROOT\""],
    )

    output = ""
    async for chunk in runner.read_pty_stream(pty_id):
        output += chunk
        if "JAIL_HOME=" in output:
            break

    assert f"JAIL_HOME={cwd}" in output
    assert f"JAIL_ROOT={cwd}" in output

    await runner.terminate_pty(pty_id)


@pytest.mark.asyncio
async def test_runner_dev_server_lifecycle():
    runner = LocalProcessRunner()
    host = await runner.create_host("proj_test_dev")
    lane = await runner.create_lane(host.host_id, "main", is_pair_lane=True)

    # Start dev server process
    srv = await runner.start_dev_server(lane.lane_id, "echo 'dev server running' && sleep 10", port=3999)
    assert srv.is_running is True
    assert srv.port == 3999

    # Stop dev server
    await runner.stop_dev_server(lane.lane_id)
    assert runner._dev_server_info[lane.lane_id].is_running is False
