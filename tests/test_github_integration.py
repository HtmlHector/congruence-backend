"""Tests for GitHub integration routes and OAuth flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

AUTH = {"Authorization": "Bearer mock_gh_user"}


@pytest.mark.asyncio
async def test_github_connect_url(client: AsyncClient) -> None:
    response = await client.get("/api/v1/integrations/github/connect", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "github"
    assert "https://github.com/login/oauth/authorize" in data["authorize_url"]
    assert "client_id=" in data["authorize_url"]
    assert "scope=" in data["authorize_url"]


@pytest.mark.asyncio
async def test_github_status_disconnected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/integrations/github/status", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert "connected" in data


@pytest.mark.asyncio
async def test_github_repos_disconnected_raises_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/integrations/github/repos", headers=AUTH)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_github_disconnect(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/integrations/github/disconnect", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["connected"] is False


@pytest.mark.asyncio
async def test_github_connect_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/v1/integrations/github/connect")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_github_callback_success(client: AsyncClient) -> None:
    # Obtain a signed OAuth state bound to the authenticated user
    connect = await client.get("/api/v1/integrations/github/connect", headers=AUTH)
    assert connect.status_code == 200
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(connect.json()["authorize_url"]).query)["state"][0]

    with patch("src.infrastructure.github.oauth.GitHubOAuth.exchange_code", new_callable=AsyncMock) as mock_exchange:
        mock_exchange.return_value = {
            "access_token": "gho_test_mock_token_12345",
            "token_type": "bearer",
            "scope": "repo,read:user",
        }
        with patch("src.infrastructure.github.client.GitHubClient.get_user", new_callable=AsyncMock) as mock_user:
            mock_user.return_value = {
                "id": 123456,
                "login": "testuser",
                "avatar_url": "https://avatars.githubusercontent.com/u/123456",
            }
            response = await client.get(
                f"/api/v1/integrations/github/callback?code=mock_code_123&state={state}"
            )
            assert response.status_code in (302, 307)
            assert "github=connected" in response.headers.get("location", "")


@pytest.mark.asyncio
async def test_github_callback_rejects_missing_state(client: AsyncClient) -> None:
    with patch("src.infrastructure.github.oauth.GitHubOAuth.exchange_code", new_callable=AsyncMock) as mock_exchange:
        mock_exchange.return_value = {"access_token": "gho_evil", "token_type": "bearer"}
        response = await client.get("/api/v1/integrations/github/callback?code=mock_code_123")
        assert response.status_code in (302, 307)
        assert "github_error=invalid_state" in response.headers.get("location", "")
        mock_exchange.assert_not_called()
