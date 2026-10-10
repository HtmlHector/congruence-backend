"""SQLAlchemy ORM models for workspaces and memberships."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.contexts.workspace.domain.entities import WorkspaceRole
from src.infrastructure.database.base import Base, CommonColumns


class WorkspaceModel(Base, CommonColumns):
    """PostgreSQL workspaces table."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)

    members: Mapped[list[WorkspaceMemberModel]] = relationship(
        "WorkspaceMemberModel",
        back_populates="workspace",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class WorkspaceMemberModel(Base, CommonColumns):
    """PostgreSQL workspace_members table."""

    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),)

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        SQLEnum(WorkspaceRole, name="workspace_role"),
        default=WorkspaceRole.MEMBER,
        nullable=False,
    )

    workspace: Mapped[WorkspaceModel] = relationship("WorkspaceModel", back_populates="members")
