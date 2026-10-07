"""SQLAlchemy ORM models for the identity schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as SA_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, TimestampMixin


class IdentityBase(Base):
    __abstract__ = True
    __table_args__ = {"schema": "identity"}


class User(IdentityBase, TimestampMixin):
    __tablename__ = "users"

    user_id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), primary_key=True, default=uuid4)
    supabase_user_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_workspace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class ApiKey(IdentityBase):
    __tablename__ = "api_keys"

    api_key_id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Role(IdentityBase):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("name", "workspace_id", name="uq_roles_name_workspace"),
        {"schema": "identity"},
    )

    role_id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    workspace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    permissions: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
