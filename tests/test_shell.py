"""Devbox shell ticket tests."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from src.config.settings import settings
from src.contexts.devbox.infrastructure.shell import (
    CLEAR_SCREEN,
    build_shell_command,
    session_exists,
)
from src.security.tickets import verify_ticket


async def _create_project(client: AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/projects",
        json={
            "name": "Shell Project",
            "repository_id": "acme/web-app",
            "default_branch": "main",
        },
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


@pytest.mark.asyncio
async def test_shell_ticket_is_bound_to_project_and_box(client: AsyncClient) -> None:
    project = await _create_project(client)

    response = await client.post(
        f"/api/v1/projects/{project['id']}/shell-ticket",
        json={"cols": 100, "rows": 30},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ws_url"].endswith(f"/api/v1/projects/{project['id']}/shell")
    assert payload["expires_in"] == settings.gateway.ticket_ttl_seconds

    claims = verify_ticket(payload["ticket"], secret=settings.gateway.ticket_secret).claims
    assert claims["purpose"] == "devbox_shell"
    assert claims["session"] is None
    assert claims["project_id"] == project["id"]
    assert claims["workspace_id"] == "00000000-0000-0000-0000-000000000001"
    assert claims["box_id"]
    assert claims["user_id"] == "clerk_test_user_1"
    assert claims["cols"] == 100
    assert claims["rows"] == 30


@pytest.mark.asyncio
async def test_shell_ticket_requires_an_existing_project(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/projects/00000000-0000-0000-0000-0000000000ff/shell-ticket",
        json={},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_shell_ticket_scopes_a_shell_to_its_worktree(client: AsyncClient) -> None:
    """A terminal opened from a worktree starts in that checkout, not the project."""
    project = await _create_project(client)
    created = await client.post(
        f"/api/v1/projects/{project['id']}/worktrees",
        json={"title": "Checkout flow"},
    )
    assert created.status_code == 201, created.text
    worktree = created.json()

    response = await client.post(
        f"/api/v1/projects/{project['id']}/shell-ticket",
        json={"worktree_id": worktree["id"], "session": "tab-scoped"},
    )
    assert response.status_code == 200, response.text
    claims = verify_ticket(response.json()["ticket"], secret=settings.gateway.ticket_secret).claims

    assert claims["worktree_id"] == worktree["id"]
    assert worktree["path"].endswith("/wt/checkout-flow")


@pytest.mark.asyncio
async def test_shell_ticket_rejects_unknown_worktree(client: AsyncClient) -> None:
    project = await _create_project(client)
    response = await client.post(
        f"/api/v1/projects/{project['id']}/shell-ticket",
        json={"worktree_id": "00000000-0000-0000-0000-0000000000ff"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_shell_ticket_carries_the_terminal_session(client: AsyncClient) -> None:
    project = await _create_project(client)

    response = await client.post(
        f"/api/v1/projects/{project['id']}/shell-ticket",
        json={"session": "tab-abc123"},
    )
    assert response.status_code == 200, response.text
    claims = verify_ticket(response.json()["ticket"], secret=settings.gateway.ticket_secret).claims
    assert claims["session"] == "tab-abc123"


@pytest.mark.asyncio
async def test_shell_ticket_rejects_a_malformed_session(client: AsyncClient) -> None:
    project = await _create_project(client)
    response = await client.post(
        f"/api/v1/projects/{project['id']}/shell-ticket",
        json={"session": "tab; rm -rf /"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_closing_a_terminal_session_rejects_a_malformed_name(
    client: AsyncClient,
) -> None:
    project = await _create_project(client)
    response = await client.delete(f"/api/v1/projects/{project['id']}/shell-sessions/tab%3Brm")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_closing_a_terminal_session_requires_the_project(
    client: AsyncClient,
) -> None:
    response = await client.delete(
        "/api/v1/projects/00000000-0000-0000-0000-0000000000ff/shell-sessions/tab-abc123"
    )
    assert response.status_code == 404


def test_shell_command_runs_docker_exec_in_the_worktree() -> None:
    command = build_shell_command("cng-abc", "/workspaces/web/wt/task")
    assert command[1:4] == ["exec", "-i", "-t"]
    assert "cng-abc" in command
    assert "/workspaces/web/wt/task" in command
    assert command[-2:] == ["bash", "-l"]
    assert "TERM=xterm-256color" in command


def test_shell_command_attaches_a_tmux_session_when_named() -> None:
    command = build_shell_command("cng-abc", "/workspaces/web/wt/task", "tab-abc123")
    assert command[-3:-1] == ["bash", "-lc"]
    script = command[-1]
    assert "tmux new-session -A -s cng-tab-abc123" in script
    assert "-c /workspaces/web/wt/task" in script
    assert "tmux set-option -g status off" in script
    # The previous, dead client for this tab's session is evicted on attach.
    assert "has-session -t cng-tab-abc123" in script
    assert "list-clients -t cng-tab-abc123" in script
    assert "tmux detach-client -t" in script
    # A shell must still start when the image has no tmux.
    assert script.rstrip().endswith("exec bash -l")


@pytest.mark.asyncio
async def test_session_exists_rejects_an_unsafe_name() -> None:
    assert await session_exists("cng-abc", "bad; name") is False


def test_clear_screen_wipes_a_restored_preview() -> None:
    # Home, erase display, erase saved lines: no stale screen behind a new shell.
    assert CLEAR_SCREEN.startswith(b"\x1b[H")
    assert b"\x1b[2J" in CLEAR_SCREEN
    assert b"\x1b[3J" in CLEAR_SCREEN


def test_shell_command_ignores_an_unsafe_session() -> None:
    command = build_shell_command("cng-abc", "/workspaces/web", "bad; name")
    assert command[-2:] == ["bash", "-l"]
