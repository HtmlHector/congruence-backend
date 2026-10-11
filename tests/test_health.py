"""Comprehensive API tests covering health, auth, workspaces, billing, and agents."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Parabox Backend"
    assert data["status"] == "online"


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("healthy", "degraded")
    assert data["database"] == "ok"


@pytest.mark.asyncio
async def test_v1_models_endpoint(client: AsyncClient):
    response = await client.get("/v1/models")
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert len(data["data"]) > 0


@pytest.mark.asyncio
async def test_auth_me_unauthorized(client: AsyncClient):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_me_mock_jwt(client: AsyncClient):
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer mock_user_42"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "user_42"
    assert data["auth_method"] == "mock"


@pytest.mark.asyncio
async def test_workspaces_list(client: AsyncClient):
    response = await client.get(
        "/api/v1/workspaces",
        headers={"Authorization": "Bearer mock_user_42"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["workspace_id"] == "ws_test_123"


@pytest.mark.asyncio
async def test_billing_workspace_status(client: AsyncClient):
    response = await client.get(
        "/api/v1/billing/workspaces/ws_test_123",
        headers={"Authorization": "Bearer mock_user_42"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["workspace_id"] == "ws_test_123"
    assert "subscription" in data


@pytest.mark.asyncio
async def test_agents_tools_list(client: AsyncClient):
    response = await client.get("/api/v1/agents/tools")
    assert response.status_code == 200
    tools = response.json()
    assert isinstance(tools, list)
    assert any(t["name"] == "system_status" for t in tools)


@pytest.mark.asyncio
async def test_agents_execute_tool(client: AsyncClient):
    response = await client.post(
        "/api/v1/agents/execute-tool",
        json={"tool_name": "system_status", "tool_args": {}},
        headers={"Authorization": "Bearer mock_user_42"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["result"]["status"] == "healthy"
