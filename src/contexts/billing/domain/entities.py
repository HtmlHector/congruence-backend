"""Billing domain entities, tiers, and statuses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.shared.base_aggregate import BaseAggregateRoot


class PlanTier(StrEnum):
    """Product subscription tiers."""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(StrEnum):
    """Stripe subscription lifecycle status."""

    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"


@dataclass
class Subscription(BaseAggregateRoot):
    """Workspace billing subscription state."""

    workspace_id: UUID | None = None
    stripe_subscription_id: str | None = None
    stripe_customer_id: str | None = None
    stripe_price_id: str | None = None
    tier: PlanTier = PlanTier.FREE
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
