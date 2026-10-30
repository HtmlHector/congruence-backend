"""SQLAlchemy ORM model for agent credentials."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.contexts.credentials.domain.entities import (
    CredentialKind,
    CredentialProvider,
    CredentialStatus,
)
from src.infrastructure.database.base import Base, CommonColumns

_ACTIVE = text("status = 'ACTIVE'")


class AgentCredentialModel(Base, CommonColumns):
    """PostgreSQL agent_credentials table with per-user RLS."""

    __tablename__ = "agent_credentials"
    __table_args__ = (
        Index(
            "one_active_credential",
            "user_id",
            "workspace_id",
            "provider",
            unique=True,
            postgresql_where=_ACTIVE,
            sqlite_where=_ACTIVE,
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[CredentialProvider] = mapped_column(
        SQLEnum(CredentialProvider, name="credential_provider"), nullable=False
    )
    kind: Mapped[CredentialKind] = mapped_column(
        SQLEnum(CredentialKind, name="credential_kind"), nullable=False
    )
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[CredentialStatus] = mapped_column(
        SQLEnum(CredentialStatus, name="credential_status"),
        nullable=False,
        default=CredentialStatus.ACTIVE,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
