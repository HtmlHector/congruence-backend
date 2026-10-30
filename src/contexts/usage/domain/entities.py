"""Usage domain entities, plan codes, and entitlement limits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.config.settings import settings
from src.shared.base_entity import BaseEntity


class PlanCode(StrEnum):
    """Billable plan codes exposed by Congruence."""

    TRIAL = "trial"
    STARTER = "starter"
    PRO = "pro"
    MAX = "max"


@dataclass(frozen=True)
class PlanLimits:
    """Entitlements granted by a plan."""

    included_seconds: int
    awake_boxes: int
    terminals_per_worktree: int


def plan_from_tier(tier: str | None) -> PlanCode:
    """Map the existing billing tier onto a Congruence plan code."""
    normalized = (tier or "").lower()
    if normalized in {"max", "enterprise"}:
        return PlanCode.MAX
    if normalized == "pro":
        return PlanCode.PRO
    if normalized == "starter":
        return PlanCode.STARTER
    return PlanCode.TRIAL


def limits_for(plan: PlanCode) -> PlanLimits:
    """Return the configured entitlements for a plan."""
    hours = {
        PlanCode.TRIAL: settings.limits.trial_hours,
        PlanCode.STARTER: settings.limits.starter_hours,
        PlanCode.PRO: settings.limits.pro_hours,
        PlanCode.MAX: settings.limits.max_hours,
    }[plan]
    boxes = {
        PlanCode.TRIAL: settings.limits.trial_boxes,
        PlanCode.STARTER: settings.limits.starter_boxes,
        PlanCode.PRO: settings.limits.pro_boxes,
        PlanCode.MAX: settings.limits.max_boxes,
    }[plan]
    terminals = 4 if plan == PlanCode.TRIAL else settings.devbox.max_terminals_per_worktree
    return PlanLimits(
        included_seconds=hours * 3600,
        awake_boxes=boxes,
        terminals_per_worktree=terminals,
    )


@dataclass
class UsageRecord(BaseEntity):
    """One metered awake-time slice for a devbox."""

    workspace_id: UUID | None = None
    project_id: UUID | None = None
    devbox_id: UUID | None = None
    kind: str = "box_awake_seconds"
    quantity: int = 0
    period_start: datetime | None = None
    period_end: datetime | None = None
    reported_to_stripe_at: datetime | None = None
