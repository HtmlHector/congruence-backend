"""Agent credential domain entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.shared.base_entity import BaseEntity


class CredentialProvider(StrEnum):
    """Upstream model providers whose tokens agents use."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class CredentialKind(StrEnum):
    """How a credential authenticates."""

    API_KEY = "api_key"
    OAUTH = "oauth"


class CredentialStatus(StrEnum):
    """Validity of a stored credential."""

    ACTIVE = "active"
    NEEDS_RECONNECT = "needs_reconnect"
    REVOKED = "revoked"


def normalize_provider(value: str) -> CredentialProvider:
    """Parse a provider identifier, rejecting unknown values."""
    try:
        return CredentialProvider(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"Unsupported credential provider '{value}'") from exc


@dataclass
class AgentCredential(BaseEntity):
    """An encrypted agent credential owned by exactly one user."""

    workspace_id: UUID | None = None
    user_id: str = ""
    provider: CredentialProvider = CredentialProvider.ANTHROPIC
    kind: CredentialKind = CredentialKind.API_KEY
    secret_ciphertext: str = ""
    label: str | None = None
    status: CredentialStatus = CredentialStatus.ACTIVE
    expires_at: datetime | None = None
    last_verified_at: datetime | None = None
