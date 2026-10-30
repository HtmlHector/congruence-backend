"""GitLab and Bitbucket: OAuth, repository access, token rotation, and webhooks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.integrations.application.service import IntegrationsService
from src.contexts.integrations.domain.entities import (
    CredentialType,
    IntegrationCredential,
    IntegrationProvider,
)
from src.contexts.integrations.domain.oauth import generate_oauth_state
from src.contexts.integrations.infrastructure.repository import IntegrationRepository
from src.security.encryption import encryption_service
from tests.conftest import TEST_WORKSPACE_ID

API_CALLBACK = "http://localhost:8001/api/v1/integrations"


async def _connect(client: AsyncClient, provider: IntegrationProvider) -> None:
    """Complete an OAuth callback for ``provider`` using the placeholder mock."""
    state = generate_oauth_state(
        workspace_id=UUID(TEST_WORKSPACE_ID),
        user_id="clerk_test_user_1",
        provider=provider,
    )
    resp = await client.get(f"/api/v1/integrations/{provider.value}/callback?code=mock&state={state}")
    assert resp.status_code == 303, resp.text
    assert "status=connected" in resp.headers["location"]


@pytest.mark.asyncio
async def test_gitlab_authorize_url_uses_gitlab_and_the_api_callback(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/integrations/gitlab/authorize")
    assert resp.status_code == 200
    data = resp.json()
    url = data["authorization_url"]
    assert url.startswith("https://gitlab.com/oauth/authorize?")
    assert data["redirect_uri"] == f"{API_CALLBACK}/gitlab/callback"
    assert "response_type=code" in url
    assert "scope=api+read_user" in url
    assert "state=" in url


@pytest.mark.asyncio
async def test_bitbucket_authorize_url_uses_bitbucket(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/integrations/bitbucket/authorize")
    assert resp.status_code == 200
    data = resp.json()
    url = data["authorization_url"]
    assert url.startswith("https://bitbucket.org/site/oauth2/authorize?")
    assert data["redirect_uri"] == f"{API_CALLBACK}/bitbucket/callback"
    assert "response_type=code" in url
    # Bitbucket scopes live on the OAuth consumer, not in the query string.
    assert "scope=" not in url


@pytest.mark.asyncio
async def test_gitlab_callback_stores_an_expiring_credential(client: AsyncClient) -> None:
    await _connect(client, IntegrationProvider.GITLAB)

    listing = await client.get("/api/v1/integrations")
    assert listing.status_code == 200
    gitlab = next(item for item in listing.json() if item["provider"] == "gitlab")
    assert gitlab["is_active"] is True
    assert "api" in gitlab["scopes"]
    assert gitlab["metadata"]["account_login"] == "mock-gitlab-user"


@pytest.mark.asyncio
async def test_bitbucket_callback_stores_credential(client: AsyncClient) -> None:
    await _connect(client, IntegrationProvider.BITBUCKET)

    listing = await client.get("/api/v1/integrations")
    assert listing.status_code == 200
    bitbucket = next(item for item in listing.json() if item["provider"] == "bitbucket")
    assert "repository" in bitbucket["scopes"]
    assert bitbucket["metadata"]["account_login"] == "mock-bitbucket-user"


@pytest.mark.asyncio
async def test_repository_listing_requires_a_connection(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/integrations/gitlab/repositories")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_repository_listing_returns_connected_repositories(client: AsyncClient) -> None:
    await _connect(client, IntegrationProvider.GITLAB)

    resp = await client.get("/api/v1/integrations/gitlab/repositories")
    assert resp.status_code == 200
    assert resp.json() == [{"full_name": "acme/demo", "private": True, "default_branch": "main"}]


@pytest.mark.asyncio
async def test_non_git_providers_cannot_list_repositories(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/integrations/slack/repositories")
    assert resp.status_code == 400
    assert "does not host git repositories" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_gitlab_token_is_rotated_before_it_expires(db_session: AsyncSession) -> None:
    workspace_id = UUID(TEST_WORKSPACE_ID)
    repo = IntegrationRepository(db_session)
    expired = IntegrationCredential(
        workspace_id=workspace_id,
        provider=IntegrationProvider.GITLAB,
        credential_type=CredentialType.OAUTH_TOKEN,
        encrypted_value=encryption_service.encrypt("stale-token"),
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
        refresh_token_encrypted=encryption_service.encrypt("refresh-me"),
    )
    await repo.upsert_credential(expired)
    await db_session.commit()

    token = await IntegrationsService(db_session).get_decrypted_token(
        workspace_id, IntegrationProvider.GITLAB
    )

    assert token == "glmock-refreshed-access-token"
    stored = await repo.get_credential(workspace_id, IntegrationProvider.GITLAB)
    assert stored is not None
    assert encryption_service.decrypt(stored.encrypted_value) == "glmock-refreshed-access-token"
    assert stored.refresh_token_encrypted is not None
    assert stored.expires_at is not None


@pytest.mark.asyncio
async def test_repository_token_uses_the_connected_provider(db_session: AsyncSession) -> None:
    workspace_id = UUID(TEST_WORKSPACE_ID)
    await IntegrationRepository(db_session).upsert_credential(
        IntegrationCredential(
            workspace_id=workspace_id,
            provider=IntegrationProvider.BITBUCKET,
            credential_type=CredentialType.OAUTH_TOKEN,
            encrypted_value=encryption_service.encrypt("bitbucket-access-token"),
        )
    )
    await db_session.commit()

    token = await IntegrationsService(db_session).get_repository_token(
        workspace_id, "acme/demo", "bitbucket"
    )

    assert token == "bitbucket-access-token"


@pytest.mark.asyncio
async def test_gitlab_webhook_requires_the_configured_token(client: AsyncClient) -> None:
    payload = b'{"object_kind":"push"}'

    with patch.object(settings.integrations, "gitlab_webhook_secret", "gl-webhook-secret"):
        rejected = await client.post(
            "/api/v1/integrations/webhooks/gitlab",
            content=payload,
            headers={"X-Gitlab-Token": "wrong"},
        )
        accepted = await client.post(
            "/api/v1/integrations/webhooks/gitlab",
            content=payload,
            headers={
                "X-Gitlab-Token": "gl-webhook-secret",
                "X-Gitlab-Event": "Push Hook",
            },
        )

    assert rejected.status_code == 400
    assert accepted.status_code == 200
    assert accepted.json()["event"] == "Push Hook"


@pytest.mark.asyncio
async def test_bitbucket_webhook_reports_the_event_key(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/integrations/webhooks/bitbucket",
        content=b'{"push":{"changes":[]}}',
        headers={"X-Event-Key": "repo:push", "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["event"] == "repo:push"
    assert resp.json()["provider"] == "bitbucket"


@pytest.mark.asyncio
async def test_projects_only_accept_known_repo_providers(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/projects", json={"name": "Widgets", "repo_provider": "sourcehut"})
    assert resp.status_code == 422
