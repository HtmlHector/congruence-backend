"""SQLAlchemy ORM models for the workspace schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import nanoid
from sqlalchemy import CheckConstraint, DateTime, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as SA_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, TimestampMixin


def generate_workspace_id() -> str:
    """Generate safe URL-friendly nanoid workspace prefix."""
    return f"ws_{nanoid.generate(size=16)}"


class WorkspaceBase(Base):
    __abstract__ = True
    __table_args__ = {"schema": "workspace"}


class Workspace(WorkspaceBase, TimestampMixin):
    __tablename__ = "workspaces"

    workspace_id: Mapped[str] = mapped_column(Text, primary_key=True, default=generate_workspace_id)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    plan_tier: Mapped[str] = mapped_column(Text, default="FREE", nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Membership(WorkspaceBase):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_membership_user_workspace"),
        {"schema": "workspace"},
    )

    membership_id: Mapped[UUID] = mapped_column(
        SA_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    role_id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Invitation(WorkspaceBase):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'EXPIRED', 'REVOKED')", name="ck_invitation_status"
        ),
        {"schema": "workspace"},
    )

    invitation_id: Mapped[UUID] = mapped_column(
        SA_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    invited_by: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role_id: Mapped[UUID] = mapped_column(SA_UUID(as_uuid=True), nullable=False)
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="PENDING", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
