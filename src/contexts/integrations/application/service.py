"""Integrations application service orchestrating OAuth flows, credentials, and webhooks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.audit.application.service import AuditService
from src.contexts.integrations.domain.entities import (
    CredentialType,
    IntegrationCredential,
    IntegrationProvider,
    WebhookEventLog,
)
from src.contexts.integrations.domain.oauth import generate_oauth_state, verify_oauth_state
from src.contexts.integrations.infrastructure.providers.bitbucket import BitbucketProvider
from src.contexts.integrations.infrastructure.providers.github import GitHubProvider
from src.contexts.integrations.infrastructure.providers.github_app import GitHubAppClient
from src.contexts.integrations.infrastructure.providers.gitlab import GitLabProvider
from src.contexts.integrations.infrastructure.providers.linear import LinearProvider
from src.contexts.integrations.infrastructure.providers.slack import SlackProvider
from src.contexts.integrations.infrastructure.repository import IntegrationRepository
from src.infrastructure.messaging.bus import message_bus
from src.realtime.events import emit_to_workspace
from src.security.encryption import encryption_service

logger = structlog.get_logger(__name__)

# Providers whose tokens can be rotated, and those that expose repositories.
RefreshableProvider = GitLabProvider | BitbucketProvider
RepositoryProvider = GitHubProvider | GitLabProvider | BitbucketProvider

# Refresh a little before expiry so a clone never races a token that dies mid-request.
REFRESH_WINDOW = timedelta(minutes=2)


def _expires_at(token_data: dict[str, Any]) -> datetime | None:
    """Convert an OAuth ``expires_in`` value into an absolute expiry."""
    expires_in = token_data.get("expires_in")
    if not isinstance(expires_in, int | float) or expires_in <= 0:
        return None
    return datetime.now(UTC) + timedelta(seconds=int(expires_in))


def _is_expiring(expires_at: datetime) -> bool:
    """True when ``expires_at`` is inside the refresh window (handles naive values)."""
    value = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    return value <= datetime.now(UTC) + REFRESH_WINDOW


def _provider_enum(value: str | None) -> IntegrationProvider | None:
    """Map a project's ``repo_provider`` string onto a known provider."""
    try:
        return IntegrationProvider((value or "github").lower())
    except ValueError:
        return None


class IntegrationsService:
    """Manages integration lifecycle, encrypted credentials, and webhooks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = IntegrationRepository(session)
        self._audit = AuditService(session)
        self._github = GitHubProvider()
        self._github_app = GitHubAppClient()
        self._gitlab = GitLabProvider()
        self._bitbucket = BitbucketProvider()
        self._slack = SlackProvider()
        self._linear = LinearProvider()

    def get_authorization_url(
        self,
        workspace_id: UUID,
        user_id: str,
        provider: IntegrationProvider,
        redirect_uri: str,
        scopes: list[str] | None = None,
    ) -> str:
        """Generate signed state token and third-party authorization URL."""
        state = generate_oauth_state(workspace_id=workspace_id, user_id=user_id, provider=provider)

        if provider == IntegrationProvider.GITHUB:
            return self._github.get_authorize_url(
                redirect_uri=redirect_uri, state=state, scopes=scopes
            )
        elif provider == IntegrationProvider.GITLAB:
            return self._gitlab.get_authorize_url(
                redirect_uri=redirect_uri, state=state, scopes=scopes
            )
        elif provider == IntegrationProvider.BITBUCKET:
            return self._bitbucket.get_authorize_url(
                redirect_uri=redirect_uri, state=state, scopes=scopes
            )
        elif provider == IntegrationProvider.SLACK:
            return self._slack.get_authorize_url(
                redirect_uri=redirect_uri, state=state, scopes=scopes
            )
        elif provider == IntegrationProvider.LINEAR:
            return self._linear.get_authorize_url(
                redirect_uri=redirect_uri, state=state, scopes=scopes
            )
        else:
            raise ValueError(f"Provider {provider} does not support OAuth authorization")

    async def handle_oauth_callback(
        self,
        state: str,
        code: str,
        redirect_uri: str,
    ) -> IntegrationCredential:
        """Verify signed state, exchange code for token, and store encrypted credential."""
        state_data = verify_oauth_state(state)
        workspace_id = UUID(state_data["ws"])
        user_id = state_data["uid"]
        provider = IntegrationProvider(state_data["prv"])

        refresh_token: str | None = None
        if provider == IntegrationProvider.GITHUB:
            token_data = await self._github.exchange_code(code=code, redirect_uri=redirect_uri)
            access_token = token_data["access_token"]
            scopes = (
                token_data.get("scope", "").split(",")
                if isinstance(token_data.get("scope"), str)
                else []
            )
            user_profile = await self._github.get_authenticated_user(access_token)
            metadata = {
                "account_login": user_profile.get("login"),
                "account_id": user_profile.get("id"),
            }
        elif provider == IntegrationProvider.GITLAB:
            token_data = await self._gitlab.exchange_code(code=code, redirect_uri=redirect_uri)
            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token")
            scopes = str(token_data.get("scope", "")).split()
            profile = await self._gitlab.get_authenticated_user(access_token)
            metadata = {
                "account_login": profile.get("username"),
                "account_id": profile.get("id"),
            }
        elif provider == IntegrationProvider.BITBUCKET:
            token_data = await self._bitbucket.exchange_code(code=code, redirect_uri=redirect_uri)
            access_token = token_data["access_token"]
            refresh_token = token_data.get("refresh_token")
            scopes = str(token_data.get("scopes", "")).split()
            profile = await self._bitbucket.get_authenticated_user(access_token)
            metadata = {
                "account_login": profile.get("username"),
                "account_id": profile.get("uuid"),
            }
        elif provider == IntegrationProvider.SLACK:
            token_data = await self._slack.exchange_code(code=code, redirect_uri=redirect_uri)
            access_token = token_data["access_token"]
            scopes = token_data.get("scope", "").split(",")
            metadata = {"team": token_data.get("team", {})}
        elif provider == IntegrationProvider.LINEAR:
            token_data = await self._linear.exchange_code(code=code, redirect_uri=redirect_uri)
            access_token = token_data["access_token"]
            scopes = token_data.get("scope", [])
            viewer = await self._linear.get_viewer(access_token)
            metadata = {"viewer": viewer}
        else:
            raise ValueError(f"Unsupported OAuth provider: {provider}")

        # Encrypt token using AES-GCM / Fernet
        encrypted_token = encryption_service.encrypt(access_token)

        cred = IntegrationCredential(
            workspace_id=workspace_id,
            provider=provider,
            credential_type=CredentialType.OAUTH_TOKEN,
            encrypted_value=encrypted_token,
            scopes=[s.strip() for s in scopes if s.strip()],
            expires_at=_expires_at(token_data),
            refresh_token_encrypted=(
                encryption_service.encrypt(refresh_token) if refresh_token else None
            ),
            metadata=metadata,
        )
        saved = await self._repo.upsert_credential(cred)

        # Audit trail
        await self._audit.record(
            workspace_id=workspace_id,
            actor_id=user_id,
            action="integration.connect",
            resource_type="integration",
            resource_id=provider.value,
            payload={"provider": provider.value, "metadata": metadata},
        )

        # Real-time event
        await emit_to_workspace(
            workspace_id=str(workspace_id),
            event_name="integration:connected",
            payload={"provider": provider.value},
        )

        return saved

    async def list_workspace_integrations(self, workspace_id: UUID) -> list[dict[str, Any]]:
        """List connected integrations for active workspace without exposing secret keys."""
        creds = await self._repo.list_credentials_for_workspace(workspace_id)
        result = []
        for c in creds:
            result.append(
                {
                    "id": str(c.id),
                    "provider": c.provider.value,
                    "credential_type": c.credential_type.value,
                    "scopes": c.scopes,
                    "metadata": c.metadata,
                    "is_active": c.is_active,
                    "created_at": c.created_at.isoformat(),
                    "updated_at": c.updated_at.isoformat(),
                }
            )
        return result

    async def disconnect_integration(
        self,
        workspace_id: UUID,
        provider: IntegrationProvider,
        actor_id: str,
    ) -> bool:
        """Disconnect and delete integration credentials for a workspace."""
        success = await self._repo.delete_credential(workspace_id, provider)
        if success:
            await self._audit.record(
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="integration.disconnect",
                resource_type="integration",
                resource_id=provider.value,
                payload={"provider": provider.value},
            )
            await emit_to_workspace(
                workspace_id=str(workspace_id),
                event_name="integration:disconnected",
                payload={"provider": provider.value},
            )
        return success

    async def store_custom_credential(
        self,
        workspace_id: UUID,
        provider: IntegrationProvider,
        raw_secret: str,
        credential_type: CredentialType = CredentialType.API_KEY,
        metadata: dict[str, Any] | None = None,
        actor_id: str = "system",
    ) -> IntegrationCredential:
        """Store an encrypted API key or custom webhook secret."""
        encrypted_token = encryption_service.encrypt(raw_secret)
        cred = IntegrationCredential(
            workspace_id=workspace_id,
            provider=provider,
            credential_type=credential_type,
            encrypted_value=encrypted_token,
            metadata=metadata or {},
        )
        saved = await self._repo.upsert_credential(cred)

        await self._audit.record(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="integration.store_credential",
            resource_type="integration",
            resource_id=provider.value,
            payload={"provider": provider.value},
        )
        return saved

    async def get_decrypted_token(
        self,
        workspace_id: UUID,
        provider: IntegrationProvider,
    ) -> str | None:
        """Fetch and decrypt a provider token, refreshing it first when it is expiring."""
        cred = await self._repo.get_credential(workspace_id, provider)
        if not cred or not cred.encrypted_value:
            return None
        if cred.expires_at and _is_expiring(cred.expires_at):
            refreshed = await self._refresh_credential(cred)
            if refreshed:
                return refreshed
        return encryption_service.decrypt(cred.encrypted_value)

    async def _refresh_credential(self, cred: IntegrationCredential) -> str | None:
        """Rotate an expiring token and persist the new pair."""
        if not cred.refresh_token_encrypted:
            return None
        refreshable: dict[IntegrationProvider, RefreshableProvider] = {
            IntegrationProvider.GITLAB: self._gitlab,
            IntegrationProvider.BITBUCKET: self._bitbucket,
        }
        client = refreshable.get(cred.provider)
        if client is None:
            return None

        try:
            token_data = await client.refresh_access_token(
                encryption_service.decrypt(cred.refresh_token_encrypted)
            )
        except Exception as exc:
            logger.warning(
                "integration_token_refresh_failed", provider=cred.provider.value, error=str(exc)
            )
            return None

        access_token = str(token_data.get("access_token") or "")
        if not access_token:
            return None
        cred.encrypted_value = encryption_service.encrypt(access_token)
        cred.expires_at = _expires_at(token_data)
        rotated = token_data.get("refresh_token")
        if rotated:
            cred.refresh_token_encrypted = encryption_service.encrypt(rotated)
        await self._repo.upsert_credential(cred)
        logger.info("integration_token_refreshed", provider=cred.provider.value)
        return access_token

    async def get_repository_token(
        self,
        workspace_id: UUID,
        repository_id: str,
        repo_provider: str = "github",
    ) -> str | None:
        """Return a token that can clone ``repository_id`` on behalf of the workspace.

        GitHub prefers an installation token (scoped to the repositories the app
        was granted) and falls back to the OAuth token; GitLab and Bitbucket use
        the workspace's stored OAuth token.
        """
        provider = _provider_enum(repo_provider)
        if provider is None:
            return None
        if provider == IntegrationProvider.GITHUB:
            token = await self._github_app.repository_token(repository_id)
            if token:
                return token
        return await self.get_decrypted_token(workspace_id, provider)

    async def list_github_repositories(self) -> list[dict[str, Any]]:
        """List repositories the GitHub App installation can reach."""
        return await self._github_app.list_repositories()

    async def list_repositories(
        self,
        provider: IntegrationProvider,
        workspace_id: UUID,
    ) -> list[dict[str, Any]]:
        """List repositories the workspace can connect for a git provider."""
        if provider == IntegrationProvider.GITHUB:
            installed = await self._github_app.list_repositories()
            if installed:
                return installed

        repository_clients: dict[IntegrationProvider, RepositoryProvider] = {
            IntegrationProvider.GITHUB: self._github,
            IntegrationProvider.GITLAB: self._gitlab,
            IntegrationProvider.BITBUCKET: self._bitbucket,
        }
        client = repository_clients.get(provider)
        if client is None:
            raise ValueError(f"Provider {provider} does not expose repositories")

        token = await self.get_decrypted_token(workspace_id, provider)
        if not token:
            return []
        return await client.list_repositories(token)

    async def handle_inbound_webhook(
        self,
        provider: IntegrationProvider,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        """Validate signature, record webhook log, and publish to RabbitMQ."""
        lowered = {key.lower(): value for key, value in headers.items()}

        # Provider-specific signature verification
        if provider == IntegrationProvider.GITHUB:
            sig = lowered.get("x-hub-signature-256", "")
            if not self._github.verify_webhook_signature(body, sig):
                raise ValueError("Invalid GitHub webhook signature")
            event_type = lowered.get("x-github-event", "unknown")
        elif provider == IntegrationProvider.GITLAB:
            if not self._gitlab.verify_webhook_token(lowered.get("x-gitlab-token", "")):
                raise ValueError("Invalid GitLab webhook token")
            event_type = lowered.get("x-gitlab-event", "unknown")
        elif provider == IntegrationProvider.BITBUCKET:
            # Bitbucket Cloud does not sign webhook payloads, so the event key is
            # the only trustworthy header; workspace scoping happens downstream.
            event_type = lowered.get("x-event-key", "unknown")
        else:
            event_type = "generic_event"

        payload: dict[str, Any] = json.loads(body.decode("utf-8")) if body else {}

        log_entry = WebhookEventLog(
            provider=provider,
            event_type=event_type,
            payload=payload,
        )
        await self._repo.log_webhook_event(log_entry)

        # Broadcast event to message bus
        await message_bus.publish(
            routing_key=f"integrations.webhook.{provider.value}",
            payload={"provider": provider.value, "event": event_type, "data": payload},
        )

        return {"status": "received", "provider": provider.value, "event": event_type}
