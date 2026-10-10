"""SQLAlchemy ORM models for third-party integrations and credentials."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.contexts.integrations.domain.entities import CredentialType, IntegrationProvider
from src.infrastructure.database.base import Base, CommonColumns


class IntegrationCredentialModel(Base, CommonColumns):
    """PostgreSQL integration_credentials table storing encrypted tokens."""

    __tablename__ = "integration_credentials"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", name="uq_workspace_provider_credential"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[IntegrationProvider] = mapped_column(
        SQLEnum(IntegrationProvider, name="integration_provider"),
        nullable=False,
        index=True,
    )
    credential_type: Mapped[CredentialType] = mapped_column(
        SQLEnum(CredentialType, name="credential_type"),
        default=CredentialType.OAUTH_TOKEN,
        nullable=False,
    )
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class IntegrationConfigModel(Base, CommonColumns):
    """PostgreSQL integration_configs table storing workspace provider settings."""

    __tablename__ = "integration_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", name="uq_workspace_provider_config"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[IntegrationProvider] = mapped_column(
        SQLEnum(IntegrationProvider, name="integration_provider"),
        nullable=False,
        index=True,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class WebhookEventModel(Base, CommonColumns):
    """PostgreSQL webhook_events table for auditing and deduplication."""

    __tablename__ = "webhook_events"

    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider: Mapped[IntegrationProvider] = mapped_column(
        SQLEnum(IntegrationProvider, name="integration_provider"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
