"""End-to-end tests for the Congruence control-plane slice."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from src.config.settings import settings
from src.contexts.worktrees.domain.entities import slugify
from src.contexts.worktrees.infrastructure.models import WorktreeModel
from src.security.tickets import verify_ticket


async def _create_project(client: AsyncClient, name: str = "Web App") -> dict[str, Any]:
    response = await client.post(
        "/api/v1/projects",
        json={
            "name": name,
            "repository_id": "repo_123",
            "default_branch": "main",
            "setup_command": "npm ci",
        },
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def _create_worktree(
    client: AsyncClient, project_id: str, title: str = "Fix flaky auth"
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/projects/{project_id}/worktrees",
        json={"title": title, "initial_prompt": "Find and fix the flaky test."},
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


@pytest.mark.asyncio
async def test_project_provisions_devbox(client: AsyncClient) -> None:
    project = await _create_project(client)
    assert project["repository_id"] == "repo_123"
    assert project["setup_status"] == "pending"
    assert project["default_branch"] == "main"

    box_response = await client.get(f"/api/v1/projects/{project['id']}/box")
    assert box_response.status_code == 200, box_response.text
    box = box_response.json()
    assert box["status"] == "awake"
    assert box["provider"] == "memory"
    assert box["cpu"] == settings.devbox.default_cpu


@pytest.mark.asyncio
async def test_deleting_project_tears_down_its_devbox(client: AsyncClient) -> None:
    """Project deletion destroys the box instead of leaking the container."""
    project = await _create_project(client)
    assert (await client.get(f"/api/v1/projects/{project['id']}/box")).status_code == 200

    deleted = await client.delete(f"/api/v1/projects/{project['id']}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/projects/{project['id']}/box")).status_code == 404


@pytest.mark.asyncio
async def test_project_creation_records_the_primary_worktree(client: AsyncClient) -> None:
    """A new project opens on its root checkout, listed first as the primary."""
    project = await _create_project(client)

    listed = await client.get(f"/api/v1/projects/{project['id']}/worktrees")
    assert listed.status_code == 200, listed.text
    worktrees = listed.json()
    assert len(worktrees) == 1

    primary = worktrees[0]
    assert primary["is_primary"] is True
    assert primary["branch"] == "main"
    assert primary["base_branch"] == "main"
    assert primary["path"] == f"/workspaces/{slugify(project['name'])}"
    assert primary["slot"] == 0
    assert primary["port_base"] == settings.devbox.worktree_port_start
    assert primary["status"] == "ready"
    assert primary["created_by"] == "clerk_test_user_1"


@pytest.mark.asyncio
async def test_primary_endpoint_backfills_a_project_without_worktrees(
    client: AsyncClient, db_session: Any
) -> None:
    """Projects created before primary worktrees existed get one on demand."""
    project = await _create_project(client)
    await db_session.execute(delete(WorktreeModel))
    await db_session.commit()
    assert (await client.get(f"/api/v1/projects/{project['id']}/worktrees")).json() == []

    response = await client.post(f"/api/v1/projects/{project['id']}/worktrees/primary")
    assert response.status_code == 200, response.text
    primary = response.json()
    assert primary["is_primary"] is True
    assert primary["branch"] == "main"
    assert primary["path"] == f"/workspaces/{slugify(project['name'])}"

    again = await client.post(f"/api/v1/projects/{project['id']}/worktrees/primary")
    assert again.status_code == 200, again.text
    assert again.json()["id"] == primary["id"]


@pytest.mark.asyncio
async def test_primary_worktree_cannot_be_deleted(client: AsyncClient) -> None:
    project = await _create_project(client)
    worktrees = (await client.get(f"/api/v1/projects/{project['id']}/worktrees")).json()
    primary = worktrees[0]

    response = await client.delete(f"/api/v1/worktrees/{primary['id']}")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "PRIMARY_WORKTREE"

    remaining = (await client.get(f"/api/v1/projects/{project['id']}/worktrees")).json()
    assert [item["id"] for item in remaining] == [primary["id"]]


@pytest.mark.asyncio
async def test_worktree_creation_allocates_slots_and_dedupes_branches(
    client: AsyncClient,
) -> None:
    project = await _create_project(client)
    first = await _create_worktree(client, project["id"], "Fix flaky auth")
    assert first["status"] == "ready"
    assert first["branch"] == "congruence/fix-flaky-auth"
    assert first["is_primary"] is False
    # Slot 0 belongs to the primary checkout, so new worktrees start after it.
    assert first["slot"] == 1
    assert first["port_base"] == (
        settings.devbox.worktree_port_start + settings.devbox.worktree_port_block
    )
    assert first["path"].startswith("/workspaces/")

    second = await _create_worktree(client, project["id"], "Fix flaky auth")
    assert second["branch"] == "congruence/fix-flaky-auth-2"
    assert second["slot"] == 2
    assert second["port_base"] == (
        settings.devbox.worktree_port_start + 2 * settings.devbox.worktree_port_block
    )

    listed = await client.get(f"/api/v1/projects/{project['id']}/worktrees")
    assert listed.status_code == 200
    worktrees = listed.json()
    assert len(worktrees) == 3
    assert worktrees[0]["is_primary"] is True
    assert {item["id"] for item in worktrees[1:]} == {first["id"], second["id"]}


@pytest.mark.asyncio
async def test_worktree_uses_the_branch_name_the_user_supplies(client: AsyncClient) -> None:
    project = await _create_project(client)

    created = await client.post(
        f"/api/v1/projects/{project['id']}/worktrees",
        json={"title": "Cart recovery email", "branch": "feature/cart-recovery-email"},
    )
    assert created.status_code == 201, created.text
    worktree = created.json()
    assert worktree["branch"] == "feature/cart-recovery-email"
    assert worktree["path"].endswith("/wt/cart-recovery-email")


@pytest.mark.asyncio
async def test_worktree_rejects_duplicate_and_default_branches(client: AsyncClient) -> None:
    project = await _create_project(client)
    payload = {"title": "Checkout flow", "branch": "feature/checkout-flow"}

    first = await client.post(f"/api/v1/projects/{project['id']}/worktrees", json=payload)
    assert first.status_code == 201, first.text

    duplicate = await client.post(
        f"/api/v1/projects/{project['id']}/worktrees",
        json={"title": "Checkout flow v2", "branch": "feature/checkout-flow"},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["detail"]["code"] == "BRANCH_EXISTS"

    default_branch = await client.post(
        f"/api/v1/projects/{project['id']}/worktrees",
        json={"title": "Main again", "branch": "main"},
    )
    assert default_branch.status_code == 409, default_branch.text
    assert default_branch.json()["detail"]["code"] == "BRANCH_EXISTS"


@pytest.mark.asyncio
async def test_pause_and_resume_accumulate_awake_time(client: AsyncClient) -> None:
    project = await _create_project(client)
    paused = await client.post(f"/api/v1/projects/{project['id']}/box/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"

    resumed = await client.post(f"/api/v1/projects/{project['id']}/box/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "awake"


@pytest.mark.asyncio
async def test_shell_terminal_attach_and_close(client: AsyncClient) -> None:
    project = await _create_project(client)
    worktree = await _create_worktree(client, project["id"])

    created = await client.post(
        f"/api/v1/worktrees/{worktree['id']}/terminals",
        json={"kind": "shell", "title": "shell"},
    )
    assert created.status_code == 201, created.text
    terminal = created.json()
    assert terminal["status"] == "open"
    assert terminal["recording_enabled"] is True
    assert terminal["tmux_session"].startswith("cng-")

    attach = await client.post(f"/api/v1/terminals/{terminal['id']}/attach")
    assert attach.status_code == 200, attach.text
    payload = attach.json()
    assert payload["expires_in"] == settings.gateway.ticket_ttl_seconds

    verified = verify_ticket(payload["ticket"], secret=settings.gateway.ticket_secret)
    assert verified.claims["purpose"] == "terminal_attach"
    assert verified.claims["terminal_id"] == terminal["id"]
    assert verified.claims["user_id"] == "clerk_test_user_1"

    closed = await client.delete(f"/api/v1/terminals/{terminal['id']}")
    assert closed.status_code == 204

    remaining = await client.get(f"/api/v1/worktrees/{worktree['id']}/terminals")
    assert remaining.json() == []


@pytest.mark.asyncio
async def test_agent_terminal_requires_credential(client: AsyncClient) -> None:
    project = await _create_project(client)
    worktree = await _create_worktree(client, project["id"])

    response = await client.post(
        f"/api/v1/worktrees/{worktree['id']}/terminals",
        json={"kind": "agent", "agent": "claude"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CREDENTIAL_MISSING"


@pytest.mark.asyncio
async def test_login_terminal_captures_oauth_and_unlocks_agent(client: AsyncClient) -> None:
    project = await _create_project(client)
    worktree = await _create_worktree(client, project["id"])

    login = await client.post(
        "/api/v1/credentials/anthropic/login", json={"worktree_id": worktree["id"]}
    )
    assert login.status_code == 200, login.text
    login_payload = login.json()
    assert login_payload["agent"] == "claude"

    terminals = await client.get(f"/api/v1/worktrees/{worktree['id']}/terminals")
    login_terminal = next(
        item for item in terminals.json() if item["id"] == login_payload["terminal_id"]
    )
    assert login_terminal["kind"] == "login"
    assert login_terminal["recording_enabled"] is False

    captured = await client.post(
        "/api/v1/credentials/anthropic/capture",
        json={
            "token": login_payload["capture_token"],
            "secret": {"access_token": "oauth-access-token", "refresh_token": "refresh"},
        },
    )
    assert captured.status_code == 200, captured.text
    credential = captured.json()
    assert credential["kind"] == "oauth"
    assert "secret" not in credential

    listed = await client.get("/api/v1/credentials")
    assert len(listed.json()) == 1

    agent = await client.post(
        f"/api/v1/worktrees/{worktree['id']}/terminals",
        json={"kind": "agent", "agent": "claude"},
    )
    assert agent.status_code == 201, agent.text
    assert agent.json()["recording_enabled"] is True


@pytest.mark.asyncio
async def test_capture_token_bound_to_provider_and_terminal(client: AsyncClient) -> None:
    project = await _create_project(client)
    worktree = await _create_worktree(client, project["id"])
    login = await client.post(
        "/api/v1/credentials/anthropic/login", json={"worktree_id": worktree["id"]}
    )
    token = login.json()["capture_token"]

    wrong_provider = await client.post(
        "/api/v1/credentials/openai/capture",
        json={"token": token, "secret": {"access_token": "x"}},
    )
    assert wrong_provider.status_code == 400

    tampered = await client.post(
        "/api/v1/credentials/anthropic/capture",
        json={"token": token + "x", "secret": {"access_token": "x"}},
    )
    assert tampered.status_code == 400


@pytest.mark.asyncio
async def test_api_key_is_stored_encrypted_and_never_returned(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/credentials",
        json={"provider": "anthropic", "value": "sk-ant-secret-value", "label": "work"},
    )
    assert response.status_code == 201, response.text
    stored = response.json()
    assert stored["kind"] == "api_key"
    assert stored["provider"] == "anthropic"
    assert "sk-ant" not in response.text

    listed = await client.get("/api/v1/credentials")
    assert listed.status_code == 200
    assert "sk-ant" not in listed.text

    revoked = await client.delete("/api/v1/credentials/anthropic")
    assert revoked.status_code == 204
    assert (await client.get("/api/v1/credentials")).json() == []


@pytest.mark.asyncio
async def test_terminal_limit_is_enforced_per_plan(client: AsyncClient) -> None:
    project = await _create_project(client)
    worktree = await _create_worktree(client, project["id"])

    trial_terminal_limit = 4  # Trial plans allow four terminals per worktree
    for index in range(trial_terminal_limit):
        created = await client.post(
            f"/api/v1/worktrees/{worktree['id']}/terminals",
            json={"kind": "shell", "title": f"shell-{index}"},
        )
        assert created.status_code == 201, created.text

    overflow = await client.post(
        f"/api/v1/worktrees/{worktree['id']}/terminals", json={"kind": "shell"}
    )
    assert overflow.status_code == 429
    assert overflow.json()["detail"]["code"] == "TERMINAL_LIMIT"


@pytest.mark.asyncio
async def test_environment_store_masks_secrets(client: AsyncClient) -> None:
    project = await _create_project(client)

    secret = await client.put(
        f"/api/v1/projects/{project['id']}/env",
        json={"key": "DATABASE_URL", "value": "postgres://user:pass@db/app", "is_secret": True},
    )
    assert secret.status_code == 200, secret.text
    assert secret.json()[0]["value"] == "********"

    public = await client.put(
        "/api/v1/env/workspace",
        json={"key": "PUBLIC_ORIGIN", "value": "https://app.test", "is_secret": False},
    )
    assert public.status_code == 200, public.text
    assert public.json()[0]["value"] == "https://app.test"

    fetched = await client.get(f"/api/v1/projects/{project['id']}/env")
    assert fetched.json()[0]["value"] == "********"
    assert "postgres://" not in fetched.text


@pytest.mark.asyncio
async def test_usage_endpoints_report_trial_plan(client: AsyncClient) -> None:
    usage = await client.get("/api/v1/usage")
    assert usage.status_code == 200, usage.text
    payload = usage.json()
    assert payload["plan"] == "trial"
    assert payload["included_seconds"] == settings.limits.trial_hours * 3600
    assert payload["awake_seconds"] == 0
    assert payload["percent_used"] == 0.0

    limits = await client.get("/api/v1/usage/limits")
    assert limits.status_code == 200
    assert limits.json()["awake_boxes"] == settings.limits.trial_boxes
    assert limits.json()["terminals_per_worktree"] == 4


@pytest.mark.asyncio
async def test_workspace_scoping_hides_other_projects(client: AsyncClient) -> None:
    project = await _create_project(client, "Hidden Project")
    response = await client.get("/api/v1/projects/00000000-0000-0000-0000-0000000000ff")
    assert response.status_code == 404

    other = await client.get(f"/api/v1/projects/{project['id']}/worktrees")
    assert other.status_code == 200
