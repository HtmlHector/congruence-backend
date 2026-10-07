"""SQLAlchemy ORM models for the subscription and billing schema."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import UUID as SA_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base, TimestampMixin


class SubscriptionBase(Base):
    __abstract__ = True
    __table_args__ = {"schema": "subscription"}


class Subscription(SubscriptionBase, TimestampMixin):
    __tablename__ = "subscriptions"

    subscription_id: Mapped[UUID] = mapped_column(
        SA_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    stripe_subscription_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    stripe_customer_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    plan_tier: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="active", nullable=False)
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
