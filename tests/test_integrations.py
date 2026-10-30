"""Tests for Integrations Hub, OAuth handshake, credentials, and webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch
from uuid import UUID

import pytest
from httpx import AsyncClient

from src.config.settings import settings
from src.contexts.integrations.domain.entities import IntegrationProvider
from src.contexts.integrations.domain.oauth import generate_oauth_state
from tests.conftest import TEST_WORKSPACE_ID


@pytest.mark.asyncio
async def test_list_integrations_initially_empty(client: AsyncClient) -> None:
    """Verify that a fresh workspace starts with no active integrations."""
    resp = await client.get("/api/v1/integrations")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.asyncio
async def test_github_authorize_url_generation(client: AsyncClient) -> None:
    """Verify GitHub OAuth authorize URL contains expected client_id and signed state."""
    redirect_uri = "https://app.parabox.so/integrations/github/callback"
    resp = await client.get(f"/api/v1/integrations/github/authorize?redirect_uri={redirect_uri}")
    assert resp.status_code == 200
    data = resp.json()
    assert "authorization_url" in data
    assert "github.com/login/oauth/authorize" in data["authorization_url"]
    assert "state=" in data["authorization_url"]
    assert data["provider"] == "github"


@pytest.mark.asyncio
async def test_github_repositories_are_empty_without_an_app(client: AsyncClient) -> None:
    """Without app credentials the repository picker degrades to an empty list."""
    resp = await client.get("/api/v1/integrations/github/repositories")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_github_authorize_ignores_an_unregistered_callback(client: AsyncClient) -> None:
    """A stale client callback is replaced with the registered one."""
    resp = await client.get(
        "/api/v1/integrations/github/authorize",
        params={"redirect_uri": "http://localhost:3000/oauth/callback?provider=github"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["redirect_uri"] == "http://localhost:8001/api/v1/integrations/github/callback"
    url = data["authorization_url"]
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8001%2Fapi%2Fv1" in url
    assert "localhost%3A3000" not in url
    # Exactly one query string: params stay inside the encoded redirect_uri.
    assert url.count("?") == 1


@pytest.mark.asyncio
async def test_store_custom_api_key_and_list(client: AsyncClient) -> None:
    """Verify creating a custom API key stores encrypted token and lists active integration."""
    payload = {
        "provider": "custom_api_key",
        "api_key": "sk_live_secret_token_12345",
        "credential_type": "api_key",
        "metadata": {"label": "Internal Service Key"},
    }
    resp = await client.post("/api/v1/integrations/custom-api-key", json=payload)
    assert resp.status_code == 201
    created = resp.json()
    assert created["provider"] == "custom_api_key"
    assert created["credential_type"] == "api_key"

    # List integrations should now include custom_api_key
    list_resp = await client.get("/api/v1/integrations")
    assert list_resp.status_code == 200
    integrations = list_resp.json()
    assert len(integrations) == 1
    assert integrations[0]["provider"] == "custom_api_key"
    assert integrations[0]["is_active"] is True


@pytest.mark.asyncio
async def test_disconnect_integration(client: AsyncClient) -> None:
    """Verify deleting an integration revokes and removes it."""
    # First store a key
    payload = {
        "provider": "github",
        "api_key": "ghp_mock_token_abc123",
        "credential_type": "api_key",
        "metadata": {},
    }
    create_resp = await client.post("/api/v1/integrations/custom-api-key", json=payload)
    assert create_resp.status_code == 201

    # Disconnect
    del_resp = await client.delete("/api/v1/integrations/github")
    assert del_resp.status_code == 204

    # List should now show no connected integrations
    list_resp = await client.get("/api/v1/integrations")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_github_authorize_defaults_to_the_api_callback(client: AsyncClient) -> None:
    """Without an explicit redirect_uri the API's own callback is used."""
    resp = await client.get("/api/v1/integrations/github/authorize")
    assert resp.status_code == 200
    data = resp.json()
    assert data["redirect_uri"] == "http://localhost:8001/api/v1/integrations/github/callback"
    assert (
        "redirect_uri=http%3A%2F%2Flocalhost%3A8001%2Fapi%2Fv1%2Fintegrations%2Fgithub%2Fcallback"
        in data["authorization_url"]
    )


@pytest.mark.asyncio
async def test_oauth_callback_success(client: AsyncClient) -> None:
    """Verify valid OAuth code exchange stores credentials and returns to the app."""
    workspace_id = UUID(TEST_WORKSPACE_ID)
    user_id = "clerk_test_user_1"

    valid_state = generate_oauth_state(
        workspace_id=workspace_id,
        user_id=user_id,
        provider=IntegrationProvider.GITHUB,
    )

    mock_token_result = {
        "access_token": "gho_mock_access_token_xyz987",
        "token_type": "bearer",
        "scope": "repo,read:user",
    }

    with patch(
        "src.contexts.integrations.infrastructure.providers.github.GitHubProvider.exchange_code",
        return_value=mock_token_result,
    ) as exchange:
        resp = await client.get(
            f"/api/v1/integrations/github/callback?code=mock_code&state={valid_state}"
        )

    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("http://localhost:3000/integrations?")
    assert "provider=github" in location
    assert "status=connected" in location
    assert "reason" not in location
    # The token exchange must use the same callback GitHub redirected to.
    assert (
        exchange.call_args.kwargs["redirect_uri"]
        == "http://localhost:8001/api/v1/integrations/github/callback"
    )


@pytest.mark.asyncio
async def test_oauth_callback_exchanges_with_the_registered_callback(client: AsyncClient) -> None:
    """A mismatched redirect_uri on the callback never reaches the token exchange."""
    valid_state = generate_oauth_state(
        workspace_id=UUID(TEST_WORKSPACE_ID),
        user_id="clerk_test_user_1",
        provider=IntegrationProvider.GITHUB,
    )

    with patch(
        "src.contexts.integrations.infrastructure.providers.github.GitHubProvider.exchange_code",
        return_value={"access_token": "gho_mock_token", "scope": "repo"},
    ) as exchange:
        resp = await client.get(
            f"/api/v1/integrations/github/callback?code=mock&state={valid_state}"
            "&redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Foauth%2Fcallback"
        )

    assert resp.status_code == 303
    assert (
        exchange.call_args.kwargs["redirect_uri"]
        == "http://localhost:8001/api/v1/integrations/github/callback"
    )

    listing = await client.get("/api/v1/integrations")
    assert listing.status_code == 200
    assert [item["provider"] for item in listing.json()] == ["github"]


@pytest.mark.asyncio
async def test_oauth_callback_invalid_state(client: AsyncClient) -> None:
    """Verify tampered or invalid state redirects back with an error reason."""
    resp = await client.get(
        "/api/v1/integrations/github/callback?code=mock_code&state=invalid.tampered.token"
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert "status=error" in location
    assert "reason=oauth_failed" in location
    # Internals stay out of the browser URL.
    assert "signature" not in location


@pytest.mark.asyncio
async def test_oauth_callback_denied_by_user(client: AsyncClient) -> None:
    """A denied authorization returns to the app without attempting an exchange."""
    resp = await client.get(
        "/api/v1/integrations/github/callback?error=access_denied&state=anything"
    )
    assert resp.status_code == 303
    assert "reason=access_denied" in resp.headers["location"]


@pytest.mark.asyncio
async def test_github_webhook_receiver_signature_verification(client: AsyncClient) -> None:
    """Verify inbound GitHub webhook validates HMAC-SHA256 signature."""
    webhook_secret = settings.integrations.github_webhook_secret or "mock_webhook_secret"
    payload_dict = {"action": "ping", "zen": "Responsive is better than fast."}
    payload_raw = json.dumps(payload_dict).encode("utf-8")

    signature = (
        "sha256="
        + hmac.new(
            webhook_secret.encode("utf-8"),
            payload_raw,
            hashlib.sha256,
        ).hexdigest()
    )

    # With matching secret configured
    with patch.object(settings.integrations, "github_webhook_secret", webhook_secret):
        resp = await client.post(
            "/api/v1/integrations/webhooks/github",
            content=payload_raw,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": signature,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "received"
        assert resp.json()["event"] == "ping"
