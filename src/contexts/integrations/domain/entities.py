"""Integrations domain entities, providers, and credential types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from src.shared.base_aggregate import BaseAggregateRoot
from src.shared.base_entity import BaseEntity


class IntegrationProvider(StrEnum):
    """Supported integration providers."""

    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    SLACK = "slack"
    LINEAR = "linear"
    WEBHOOK = "webhook"
    CUSTOM_API_KEY = "custom_api_key"


class CredentialType(StrEnum):
    """Types of credentials stored."""

    OAUTH_TOKEN = "oauth_token"
    API_KEY = "api_key"
    WEBHOOK_SECRET = "webhook_secret"


@dataclass
class IntegrationCredential(BaseAggregateRoot):
    """Encrypted third-party credential scoped to a workspace."""

    workspace_id: UUID | None = None
    provider: IntegrationProvider = IntegrationProvider.GITHUB
    credential_type: CredentialType = CredentialType.OAUTH_TOKEN
    encrypted_value: str = ""
    scopes: list[str] = field(default_factory=list)
    expires_at: datetime | None = None
    # Rotation token for providers whose access tokens expire (GitLab, Bitbucket).
    refresh_token_encrypted: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True


@dataclass
class IntegrationConfig(BaseEntity):
    """Workspace-level settings and toggles for an integration."""

    workspace_id: UUID | None = None
    provider: IntegrationProvider = IntegrationProvider.GITHUB
    is_enabled: bool = True
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class WebhookEventLog(BaseEntity):
    """Idempotency log of an inbound provider webhook event."""

    workspace_id: UUID | None = None
    provider: IntegrationProvider = IntegrationProvider.GITHUB
    event_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    processed_at: datetime | None = None
