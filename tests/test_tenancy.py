"""Workspace tenancy enforcement tests.

These lock in that cross-tenant access is denied even when DEBUG=true
(the old behavior auto-granted OWNER on any workspace) and that the newly
authenticated routes reject anonymous callers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.infrastructure.database.session import get_db
from src.main import app
from tests.conftest import MockAsyncSession, MockResult

AUTH = {"Authorization": "Bearer mock_tenancy_user"}


class EmptyDbSession(MockAsyncSession):
    """A session where the target workspace exists but the caller is a member of nothing."""

    async def execute(self, statement, params=None):
        stmt = str(statement)
        if "workspace.workspaces" in stmt:
            # The workspace exists — under the old debug auto-heal this alone
            # was enough to be granted OWNER.
            return MockResult([("ws_victim",)])
        return MockResult([])


@pytest.fixture
async def outsider_client(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """Client whose DB sees no memberships (simulates a user in no workspace)."""
    original = app.dependency_overrides.get(get_db)

    async def override() -> AsyncIterator[EmptyDbSession]:
        yield EmptyDbSession()

    app.dependency_overrides[get_db] = override
    try:
        yield client
    finally:
        if original is not None:
            app.dependency_overrides[get_db] = original
        else:
            app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_non_member_gets_403_even_in_debug_mode(outsider_client: AsyncClient) -> None:
    """DEBUG=true must not auto-grant OWNER on arbitrary workspaces."""
    resp = await outsider_client.get("/api/v1/workspaces/ws_victim_workspace", headers=AUTH)
    assert resp.status_code == 403
    assert "Not a member" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_storage_download_foreign_workspace_key_denied(outsider_client: AsyncClient) -> None:
    resp = await outsider_client.get(
        "/api/v1/storage/download-url",
        params={"key": "workspaces/ws_victim/assets/secret.png"},
        headers=AUTH,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_storage_download_unscoped_key_denied(outsider_client: AsyncClient) -> None:
    resp = await outsider_client.get(
        "/api/v1/storage/download-url",
        params={"key": "not-a-workspace-key.png"},
        headers=AUTH,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_storage_upload_foreign_workspace_denied(outsider_client: AsyncClient) -> None:
    resp = await outsider_client.post(
        "/api/v1/storage/upload-url",
        json={"workspace_id": "ws_victim", "file_name": "a.png"},
        headers=AUTH,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_storage_delete_foreign_workspace_key_denied(outsider_client: AsyncClient) -> None:
    resp = await outsider_client.delete(
        "/api/v1/storage/file",
        params={"key": "workspaces/ws_victim/assets/secret.png"},
        headers=AUTH,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_project_cross_tenant_returns_404(outsider_client: AsyncClient) -> None:
    """Projects outside the caller's org are indistinguishable from missing."""
    resp = await outsider_client.get("/api/v1/projects/proj_victim", headers=AUTH)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_agent_chat_stream_requires_auth(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/agents/chat/stream",
        json={"prompt": "hi", "harness": "Shell"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_agent_chat_stream_cwd_requires_project(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/agents/chat/stream",
        json={"prompt": "hi", "harness": "Shell", "cwd": "/etc"},
        headers=AUTH,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_agent_chat_stream_cwd_outside_workspace_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/agents/chat/stream",
        json={
            "prompt": "hi",
            "harness": "Shell",
            "project_id": "proj_owned_by_someone_else",
            "cwd": "/etc",
        },
        headers=AUTH,
    )
    # Either the project is inaccessible (404) or the cwd escapes (400).
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_integrations_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/integrations/github/status")).status_code == 401
    assert (await client.get("/api/v1/integrations/github/repos")).status_code == 401
    assert (await client.delete("/api/v1/integrations/github/disconnect")).status_code == 401
    assert (await client.get("/api/v1/integrations/status/proj_x")).status_code == 401


def test_websocket_rejects_anonymous_connection() -> None:
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/ws/session/sess_any_lane"
    ):
        pass


def test_websocket_rejects_bad_token() -> None:
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        "/ws/session/sess_any_lane?token=not.a.valid.token"
    ):
        pass


@pytest.mark.asyncio
async def test_preview_proxy_requires_token(client: AsyncClient) -> None:
    resp = await client.get("/preview/lane_x/3000/index.html")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_preview_proxy_rejects_bad_token(client: AsyncClient) -> None:
    resp = await client.get(
        "/preview/lane_x/3000/index.html", headers={"Authorization": "Bearer bogus.token.here"}
    )
    assert resp.status_code == 401


class SequenceSession(MockAsyncSession):
    """Returns a scripted sequence of results (for lane_access unit tests)."""

    def __init__(self, results: list[MockResult]) -> None:
        super().__init__()
        self._results = list(results)
        self._idx = 0

    async def execute(self, statement, params=None):
        result = self._results[self._idx] if self._idx < len(self._results) else MockResult([])
        self._idx += 1
        return result


@pytest.mark.asyncio
async def test_lane_access_denies_lane_from_foreign_project() -> None:
    from src.infrastructure.auth.jwt import UserClaims
    from src.infrastructure.auth.tenancy import lane_access

    user = UserClaims(user_id="user_attacker", email="attacker@example.com")

    lane = SimpleNamespace(id="lane_victim", host_id="host_1")
    host = SimpleNamespace(id="host_1", project_id="proj_1")
    project = SimpleNamespace(id="proj_1", organization_id="org_victim")

    # 1) lane lookup 2) host lookup 3) project lookup 4) org membership (empty)
    session = SequenceSession(
        [
            MockResult([lane]),
            MockResult([host]),
            MockResult([project]),
            MockResult([]),
        ]
    )
    assert await lane_access(session, user, "lane_victim") is False

    # Unknown lane → None (no tenant resource)
    session_unknown = SequenceSession([MockResult([])])
    assert await lane_access(session_unknown, user, "lane_ghost") is None

    # Membership present → True
    session_member = SequenceSession(
        [
            MockResult([lane]),
            MockResult([host]),
            MockResult([project]),
            MockResult([("proj_1",)]),
        ]
    )
    assert await lane_access(session_member, user, "lane_victim") is True
