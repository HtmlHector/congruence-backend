"""SQLAlchemy ORM model for worktrees."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.contexts.worktrees.domain.entities import WorktreeStatus
from src.infrastructure.database.base import Base, CommonColumns

_NOT_DELETED = text("deleted_at IS NULL")


class WorktreeModel(Base, CommonColumns):
    """PostgreSQL worktrees table with RLS enforcement."""

    __tablename__ = "worktrees"
    __table_args__ = (
        Index(
            "worktrees_branch_live",
            "project_id",
            "branch",
            unique=True,
            postgresql_where=_NOT_DELETED,
            sqlite_where=_NOT_DELETED,
        ),
        Index(
            "worktrees_path_live",
            "project_id",
            "path",
            unique=True,
            postgresql_where=_NOT_DELETED,
            sqlite_where=_NOT_DELETED,
        ),
        Index(
            "worktrees_slot_live",
            "project_id",
            "slot",
            unique=True,
            postgresql_where=_NOT_DELETED,
            sqlite_where=_NOT_DELETED,
        ),
        Index(
            "worktrees_one_primary",
            "project_id",
            unique=True,
            postgresql_where=text("is_primary AND deleted_at IS NULL"),
            sqlite_where=text("is_primary AND deleted_at IS NULL"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    devbox_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("devboxes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    initial_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    port_base: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[WorktreeStatus] = mapped_column(
        SQLEnum(WorktreeStatus, name="worktree_status"),
        nullable=False,
        default=WorktreeStatus.CREATING,
        index=True,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
