"""SQLAlchemy ORM model for terminals."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Uuid,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.contexts.terminals.domain.entities import TerminalKind, TerminalStatus
from src.infrastructure.database.base import Base, CommonColumns


class TerminalModel(Base, CommonColumns):
    """PostgreSQL terminals table with RLS enforcement."""

    __tablename__ = "terminals"
    __table_args__ = (
        CheckConstraint(
            "kind <> 'LOGIN' OR recording_enabled = false",
            name="login_never_recorded",
        ),
        Index("terminals_tmux", "devbox_id", "tmux_session", unique=True),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    worktree_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("worktrees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    devbox_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("devboxes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tmux_session: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[TerminalKind] = mapped_column(
        SQLEnum(TerminalKind, name="terminal_kind"), nullable=False, default=TerminalKind.SHELL
    )
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[TerminalStatus] = mapped_column(
        SQLEnum(TerminalStatus, name="terminal_status"),
        nullable=False,
        default=TerminalStatus.STARTING,
        index=True,
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    recording_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cols: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=120)
    rows: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=32)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    scrollback_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_attached_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TerminalChunkModel(Base, CommonColumns):
    """Append-only, compressed transcript chunks for a terminal."""

    __tablename__ = "terminal_chunks"
    __table_args__ = (Index("terminal_chunks_terminal_seq", "terminal_id", "seq", unique=True),)

    terminal_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("terminals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
