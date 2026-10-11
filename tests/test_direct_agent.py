import pytest
import os
import tempfile
from pathlib import Path
from src.infrastructure.tools.workspace_tools import (
    execute_read_file,
    execute_write_file,
    execute_list_dir,
    execute_run_command,
    dispatch_tool_call,
)
from src.infrastructure.llm.direct_agent import run_direct_agent_stream


@pytest.mark.asyncio
async def test_workspace_tools_file_operations():
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Write file
        res = await execute_write_file(tmp_dir, "test.txt", "hello world\nsecond line")
        assert "Successfully wrote" in res

        # Read file
        content = await execute_read_file(tmp_dir, "test.txt")
        assert "hello world" in content
        assert "second line" in content

        # List directory
        dir_res = await execute_list_dir(tmp_dir)
        assert "test.txt" in dir_res

        # Safe path traversal protection
        outside_res = await execute_read_file(tmp_dir, "../outside.txt")
        assert "Error" in outside_res or "denied" in outside_res


@pytest.mark.asyncio
async def test_workspace_tools_shell_command():
    with tempfile.TemporaryDirectory() as tmp_dir:
        res = await execute_run_command(tmp_dir, "echo 'testing tool execution'")
        assert "testing tool execution" in res


@pytest.mark.asyncio
async def test_direct_agent_missing_key_warning(monkeypatch):
    monkeypatch.setattr("src.infrastructure.llm.direct_agent._read_cli_auth", lambda _: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with tempfile.TemporaryDirectory() as tmp_dir:
        events = []
        async for evt in run_direct_agent_stream(
            harness="Antigravity",
            prompt="Hello",
            working_dir=tmp_dir,
            extra_env={}, # No API key
        ):
            events.append(evt)

        assert any(e.get("type") == "error" and "No API key configured" in e.get("message", "") for e in events)


@pytest.mark.asyncio
async def test_direct_agent_shell_stream():
    with tempfile.TemporaryDirectory() as tmp_dir:
        events = []
        async for evt in run_direct_agent_stream(
            harness="Shell",
            prompt="echo 'direct shell test'",
            working_dir=tmp_dir,
        ):
            events.append(evt)

        types = [e.get("type") for e in events]
        assert "status" in types
        assert "chunk" in types
        assert "done" in types
        assert any("direct shell test" in e.get("text", "") for e in events if e.get("type") in ("chunk", "done"))
