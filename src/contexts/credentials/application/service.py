"""Agent credential application service."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.audit.application.service import AuditService
from src.contexts.credentials.domain.entities import (
    AgentCredential,
    CredentialKind,
    CredentialStatus,
    normalize_provider,
)
from src.contexts.credentials.infrastructure.repository import CredentialRepository
from src.contexts.terminals.domain.entities import TerminalKind
from src.contexts.terminals.infrastructure.models import TerminalModel
from src.security.encryption import encryption_service
from src.security.tickets import TicketError, verify_ticket

logger = structlog.get_logger(__name__)


class CredentialError(Exception):
    """Domain-level credential failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CredentialService:
    """Stores agent credentials encrypted, scoped to one user."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CredentialRepository(session)
        self._audit = AuditService(session)

    async def list_for_user(self, workspace_id: UUID, user_id: str) -> list[AgentCredential]:
        """List a user's credentials without exposing secrets."""
        return list(await self._repo.list_for_user(workspace_id, user_id))

    async def get_active(
        self, *, workspace_id: UUID, user_id: str, provider: str
    ) -> AgentCredential | None:
        """Return the active credential for a user and provider."""
        return await self._repo.get_active(
            workspace_id=workspace_id, user_id=user_id, provider=provider
        )

    async def store_api_key(
        self,
        *,
        workspace_id: UUID,
        user_id: str,
        provider: str,
        value: str,
        label: str | None = None,
    ) -> AgentCredential:
        """Encrypt and upsert an API key credential."""
        if not value.strip():
            raise CredentialError("INVALID_CREDENTIAL", "Credential value is empty")
        normalized = normalize_provider(provider)
        return await self._upsert(
            workspace_id=workspace_id,
            user_id=user_id,
            provider=normalized.value,
            kind=CredentialKind.API_KEY,
            secret=value.strip(),
            label=label,
        )

    async def capture_from_login(
        self, *, token: str, provider: str, secret_payload: object
    ) -> AgentCredential:
        """Validate a capture token and store the OAuth credential reported by box-agent."""
        try:
            verified = verify_ticket(token, secret=settings.devbox.capture_token_secret)
        except TicketError as exc:
            raise CredentialError(
                "CAPTURE_TOKEN_INVALID", "Invalid or expired capture token"
            ) from exc

        claims = verified.claims
        if claims.get("purpose") != "credential_capture":
            raise CredentialError("CAPTURE_TOKEN_INVALID", "Ticket is not a capture token")

        normalized = normalize_provider(provider)
        if claims.get("provider") != normalized.value:
            raise CredentialError("CAPTURE_TOKEN_INVALID", "Provider does not match ticket")

        try:
            terminal_id = UUID(str(claims.get("terminal_id")))
        except (TypeError, ValueError) as exc:
            raise CredentialError("CAPTURE_TOKEN_INVALID", "Ticket has no terminal") from exc

        terminal = await self._session.get(TerminalModel, terminal_id)
        if terminal is None or terminal.kind is not TerminalKind.LOGIN:
            raise CredentialError("CAPTURE_TOKEN_INVALID", "Login terminal not found")
        if terminal.workspace_id != UUID(str(claims.get("workspace_id"))):
            raise CredentialError("CAPTURE_TOKEN_INVALID", "Workspace mismatch")
        if terminal.created_by != claims.get("user_id"):
            raise CredentialError("CAPTURE_TOKEN_INVALID", "Terminal owner mismatch")
        if terminal.closed_at is not None:
            raise CredentialError("CAPTURE_TOKEN_INVALID", "Login terminal is closed")

        payload = (
            json.dumps(secret_payload, separators=(",", ":"), sort_keys=True)
            if not isinstance(secret_payload, str)
            else secret_payload
        )

        credential = await self._upsert(
            workspace_id=terminal.workspace_id,
            user_id=terminal.created_by,
            provider=normalized.value,
            kind=CredentialKind.OAUTH,
            secret=payload,
        )

        terminal.closed_at = datetime.now(UTC)
        await self._session.flush()

        await self._audit.record(
            workspace_id=terminal.workspace_id,
            actor_id=terminal.created_by,
            action="credential.capture",
            resource_type="credential",
            resource_id=str(credential.id),
            payload={"provider": normalized.value, "terminal_id": str(terminal.id)},
        )
        return credential

    async def get_secret(self, *, workspace_id: UUID, user_id: str, provider: str) -> str | None:
        """Decrypt a user's active credential for injection into their terminal."""
        credential = await self.get_active(
            workspace_id=workspace_id, user_id=user_id, provider=provider
        )
        if credential is None:
            return None
        return encryption_service.decrypt(credential.secret_ciphertext)

    async def mark_needs_reconnect(
        self, *, workspace_id: UUID, user_id: str, provider: str
    ) -> AgentCredential | None:
        """Flag a credential after an authentication failure."""
        credential = await self.get_active(
            workspace_id=workspace_id, user_id=user_id, provider=provider
        )
        if credential is None:
            return None
        credential.status = CredentialStatus.NEEDS_RECONNECT
        return await self._repo.update(credential)

    async def revoke(
        self, *, workspace_id: UUID, user_id: str, provider: str, actor_id: str
    ) -> bool:
        """Revoke a stored credential without exposing its secret."""
        credential = await self.get_active(
            workspace_id=workspace_id, user_id=user_id, provider=provider
        )
        if credential is None:
            return False
        credential.status = CredentialStatus.REVOKED
        credential.secret_ciphertext = ""
        await self._repo.update(credential)
        await self._audit.record(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="credential.revoke",
            resource_type="credential",
            resource_id=str(credential.id),
            payload={"provider": provider},
        )
        return True

    async def _upsert(
        self,
        *,
        workspace_id: UUID,
        user_id: str,
        provider: str,
        kind: CredentialKind,
        secret: str,
        label: str | None = None,
    ) -> AgentCredential:
        existing = await self._repo.get_active(
            workspace_id=workspace_id, user_id=user_id, provider=provider
        )
        ciphertext = encryption_service.encrypt(secret)

        if existing:
            existing.secret_ciphertext = ciphertext
            existing.kind = kind
            existing.status = CredentialStatus.ACTIVE
            existing.label = label or existing.label
            return await self._repo.update(existing)

        credential = AgentCredential(
            workspace_id=workspace_id,
            user_id=user_id,
            provider=normalize_provider(provider),
            kind=kind,
            secret_ciphertext=ciphertext,
            label=label,
            status=CredentialStatus.ACTIVE,
        )
        saved = await self._repo.add(credential)
        await self._audit.record(
            workspace_id=workspace_id,
            actor_id=user_id,
            action="credential.store",
            resource_type="credential",
            resource_id=str(saved.id),
            payload={"provider": provider, "kind": kind.value},
        )
        return saved
