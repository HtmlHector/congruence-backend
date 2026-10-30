"""SQLAlchemy ORM model for project devboxes."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Uuid, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.contexts.devbox.domain.entities import DevboxStatus
from src.infrastructure.database.base import Base, CommonColumns

_LIVE_STATUS_FILTER = text("status NOT IN ('DESTROYED', 'ERROR')")


class DevboxModel(Base, CommonColumns):
    """PostgreSQL devboxes table with RLS enforcement."""

    __tablename__ = "devboxes"
    __table_args__ = (
        Index(
            "devboxes_one_live_per_project",
            "project_id",
            unique=True,
            postgresql_where=_LIVE_STATUS_FILTER,
            sqlite_where=_LIVE_STATUS_FILTER,
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
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_box_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    region: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    status: Mapped[DevboxStatus] = mapped_column(
        SQLEnum(DevboxStatus, name="devbox_status"),
        nullable=False,
        default=DevboxStatus.PROVISIONING,
        index=True,
    )
    image_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    cpu: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=4096)
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    awake_seconds_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
