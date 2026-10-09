"""Tests for DockerHostRunner and container execution engine."""

import tempfile
import pytest
from unittest.mock import AsyncMock, patch

from src.infrastructure.runner.docker import DockerHostRunner
from src.infrastructure.runner.registry import get_runner, _runner_instance


@pytest.mark.asyncio
async def test_docker_runner_lifecycle_and_fallback():
    with tempfile.TemporaryDirectory() as tmpdir:
        runner = DockerHostRunner(base_workdir=tmpdir)

        # 1. Test host creation (container mock/spawn fallback)
        host = await runner.create_host("proj_docker_123")
        assert host.host_id.startswith("host_")
        assert host.backend_type == "process_docker"
        assert host.state == "awake"

        # 2. Test lane creation and worktree resolution
        lane = await runner.create_lane(host.host_id, "feature/docker-exec", is_pair_lane=True)
        assert lane.lane_id.startswith("lane_")
        assert lane.branch_name == "feature/docker-exec"

        cwd = await runner.resolve_lane_cwd(lane.lane_id)
        assert tmpdir in cwd

        # 3. Test lane file operations
        await runner.write_lane_file(lane.lane_id, "hello.txt", "Hello Docker World")
        content = await runner.read_lane_file(lane.lane_id, "hello.txt")
        assert content == "Hello Docker World"

        file_tree = await runner.list_lane_files(lane.lane_id)
        assert "tree" in file_tree
        assert any(c["name"] == "hello.txt" for c in file_tree["tree"])

        # 4. Test diff computation
        diff = await runner.get_diff(lane.lane_id)
        assert diff.lane_id == lane.lane_id

        # 5. Test sleep / wake state
        slept = await runner.sleep_host(host.host_id)
        assert slept.state == "asleep"
        assert await runner.get_host_state(host.host_id) == "asleep"

        woken = await runner.wake_host(host.host_id)
        assert woken.state == "awake"
        assert await runner.get_host_state(host.host_id) == "awake"

        # 6. Test PTY interactive stream
        pty_id = await runner.spawn_pty(lane.lane_id, ["/bin/sh", "-c", "echo 'DOCKER_RUNNER_PTY_OK'"])
        assert pty_id.startswith("pty_")

        output = ""
        async for chunk in runner.read_pty_stream(pty_id):
            output += chunk
            if "DOCKER_RUNNER_PTY_OK" in output:
                break

        assert "DOCKER_RUNNER_PTY_OK" in output
        await runner.terminate_pty(pty_id)


@pytest.mark.asyncio
async def test_docker_runner_availability_check():
    # Test availability check returns bool
    available = await DockerHostRunner.is_docker_available()
    assert isinstance(available, bool)


def test_runner_registry_selection(monkeypatch):
    import src.infrastructure.runner.registry as reg_module
    
    # Reset singleton
    reg_module._runner_instance = None
    monkeypatch.setenv("CONGRUENCE_RUNNER_TYPE", "docker")
    runner = reg_module.get_runner()
    assert isinstance(runner, DockerHostRunner)

    reg_module._runner_instance = None
    monkeypatch.setenv("CONGRUENCE_RUNNER_TYPE", "process_local")
    local_runner = reg_module.get_runner()
    assert local_runner.__class__.__name__ == "LocalProcessRunner"
