"""SQLAlchemy ORM model for usage records."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, CommonColumns


class UsageRecordModel(Base, CommonColumns):
    """PostgreSQL usage_records table with RLS enforcement."""

    __tablename__ = "usage_records"
    __table_args__ = (
        Index(
            "usage_no_double_count",
            "devbox_id",
            "period_start",
            "period_end",
            unique=True,
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    devbox_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="box_awake_seconds")
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reported_to_stripe_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
