"""Usage application service: metering and entitlement enforcement."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.billing.infrastructure.models import SubscriptionModel
from src.contexts.usage.domain.entities import (
    PlanCode,
    PlanLimits,
    UsageRecord,
    limits_for,
    plan_from_tier,
)
from src.contexts.usage.infrastructure.repository import UsageRepository


class UsageLimitError(Exception):
    """Raised when a workspace exceeds a plan entitlement."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def current_period_start(now: datetime | None = None) -> datetime:
    """Return the first instant of the current UTC month."""
    moment = now or datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class UsageService:
    """Resolves plan limits and records metered awake time."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = UsageRepository(session)

    async def plan_for_workspace(self, workspace_id: UUID) -> PlanCode:
        """Return the workspace's plan based on its billing tier."""
        query = select(SubscriptionModel.tier).where(SubscriptionModel.workspace_id == workspace_id)
        result = await self._session.execute(query)
        tier = result.scalar_one_or_none()
        return plan_from_tier(str(tier) if tier else None)

    async def limits_for_workspace(self, workspace_id: UUID) -> PlanLimits:
        """Return the entitlements for a workspace's plan."""
        return limits_for(await self.plan_for_workspace(workspace_id))

    async def used_seconds(self, workspace_id: UUID, *, now: datetime | None = None) -> int:
        """Return metered awake seconds in the current billing period."""
        return await self._repo.total_seconds_since(workspace_id, current_period_start(now))

    async def remaining_seconds(self, workspace_id: UUID) -> int:
        """Return included seconds minus metered seconds, floored at zero."""
        limits = await self.limits_for_workspace(workspace_id)
        used = await self.used_seconds(workspace_id)
        return max(0, limits.included_seconds - used)

    async def summary(self, workspace_id: UUID) -> dict[str, int | str | float]:
        """Return a usage summary suitable for the usage endpoint."""
        limits = await self.limits_for_workspace(workspace_id)
        used = await self.used_seconds(workspace_id)
        percent = 0.0
        if limits.included_seconds:
            percent = round(min(100.0, (used / limits.included_seconds) * 100), 2)
        return {
            "plan": await self.plan_for_workspace(workspace_id),
            "awake_seconds": used,
            "included_seconds": limits.included_seconds,
            "remaining_seconds": max(0, limits.included_seconds - used),
            "percent_used": percent,
            "awake_boxes": limits.awake_boxes,
            "terminals_per_worktree": limits.terminals_per_worktree,
        }

    async def record_awake_slice(
        self,
        *,
        workspace_id: UUID,
        project_id: UUID,
        devbox_id: UUID,
        seconds: int,
        period_start: datetime,
        period_end: datetime,
    ) -> UsageRecord:
        """Persist one metered slice (idempotent by devbox + boundaries)."""
        record = UsageRecord(
            workspace_id=workspace_id,
            project_id=project_id,
            devbox_id=devbox_id,
            quantity=max(0, seconds),
            period_start=period_start,
            period_end=period_end,
        )
        return await self._repo.add(record)

    async def ensure_box_allowed(self, workspace_id: UUID, live_boxes: int) -> None:
        """Reject provisioning when the plan's awake-box cap is reached."""
        limits = await self.limits_for_workspace(workspace_id)
        if live_boxes >= limits.awake_boxes:
            raise UsageLimitError(
                "USAGE_LIMIT",
                f"Plan allows {limits.awake_boxes} awake devboxes; pause or upgrade first.",
            )

    async def ensure_usage_available(self, workspace_id: UUID) -> None:
        """Reject new work when the workspace has exhausted included hours."""
        limits = await self.limits_for_workspace(workspace_id)
        used = await self.used_seconds(workspace_id)
        if used >= limits.included_seconds:
            raise UsageLimitError(
                "USAGE_LIMIT",
                "Included devbox hours are exhausted; buy an hour pack or upgrade to continue.",
            )

    async def ensure_worktree_allowed(self, active_worktrees: int) -> None:
        """Reject worktree creation above the per-project cap."""
        if active_worktrees >= settings.devbox.max_worktrees_per_project:
            raise UsageLimitError(
                "WORKTREE_LIMIT",
                f"Projects allow at most {settings.devbox.max_worktrees_per_project} worktrees.",
            )
