"""Integrations application service orchestrating OAuth flows, credentials, and webhooks."""

from __future__ import annotations

import json
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
from src.contexts.integrations.infrastructure.providers.github import GitHubProvider
from src.contexts.integrations.infrastructure.providers.linear import LinearProvider
from src.contexts.integrations.infrastructure.providers.slack import SlackProvider
from src.contexts.integrations.infrastructure.repository import IntegrationRepository
from src.infrastructure.messaging.bus import message_bus
from src.realtime.events import emit_to_workspace
from src.security.encryption import encryption_service

logger = structlog.get_logger(__name__)


class IntegrationsService:
    """Manages integration lifecycle, encrypted credentials, and webhooks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = IntegrationRepository(session)
        self._audit = AuditService(session)
        self._github = GitHubProvider()
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
        """Fetch and decrypt token for internal service use."""
        cred = await self._repo.get_credential(workspace_id, provider)
        if not cred or not cred.encrypted_value:
            return None
        return encryption_service.decrypt(cred.encrypted_value)

    async def handle_inbound_webhook(
        self,
        provider: IntegrationProvider,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        """Validate signature, record webhook log, and publish to RabbitMQ."""
        # Provider-specific signature verification
        if provider == IntegrationProvider.GITHUB:
            sig = headers.get("x-hub-signature-256", "")
            if not self._github.verify_webhook_signature(body, sig):
                raise ValueError("Invalid GitHub webhook signature")
            event_type = headers.get("x-github-event", "unknown")
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
