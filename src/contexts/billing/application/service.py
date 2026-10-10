"""Billing application service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.billing.domain.entities import PlanTier, Subscription, SubscriptionStatus
from src.contexts.billing.infrastructure.repository import SubscriptionRepository
from src.contexts.workspace.infrastructure.repository import WorkspaceRepository
from src.infrastructure.billing.stripe import stripe_service

logger = structlog.get_logger(__name__)


class BillingService:
    """Manages Stripe checkout sessions, portal sessions, and webhook lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._sub_repo = SubscriptionRepository(session)
        self._ws_repo = WorkspaceRepository(session)

    async def get_or_create_subscription(self, workspace_id: UUID) -> Subscription:
        """Fetch subscription for workspace, creating a default FREE tier record if absent."""
        sub = await self._sub_repo.get_by_workspace_id(workspace_id)
        if sub:
            return sub

        new_sub = Subscription(
            workspace_id=workspace_id,
            tier=PlanTier.FREE,
            status=SubscriptionStatus.ACTIVE,
        )
        return await self._sub_repo.add(new_sub)

    async def create_checkout_session(
        self,
        workspace_id: UUID,
        tier: PlanTier,
        success_url: str,
        cancel_url: str,
    ) -> dict[str, str]:
        """Generate hosted Stripe Checkout URL for subscription upgrade."""
        workspace = await self._ws_repo.get_by_id(workspace_id)
        if not workspace:
            raise ValueError(f"Workspace {workspace_id} not found")

        # Map tier to Stripe Price ID
        price_id = (
            settings.stripe.pro_price_id
            if tier == PlanTier.PRO
            else settings.stripe.enterprise_price_id
        )

        customer_id = workspace.stripe_customer_id
        if not customer_id:
            customer_id = stripe_service.create_customer(
                email=f"billing+{workspace.slug}@parabox.so",
                name=workspace.name,
                metadata={"workspace_id": str(workspace_id)},
            )
            workspace.stripe_customer_id = customer_id
            await self._ws_repo.update(workspace)

        return stripe_service.create_checkout_session(
            customer_id=customer_id,
            price_id=price_id,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"workspace_id": str(workspace_id), "tier": tier.value},
        )

    async def create_portal_session(self, workspace_id: UUID, return_url: str) -> dict[str, str]:
        """Generate Stripe Customer Portal URL."""
        workspace = await self._ws_repo.get_by_id(workspace_id)
        if not workspace or not workspace.stripe_customer_id:
            raise ValueError("Workspace does not have an active billing customer record")

        return stripe_service.create_portal_session(
            customer_id=workspace.stripe_customer_id,
            return_url=return_url,
        )

    async def handle_stripe_webhook(self, event_type: str, data_object: dict[str, Any]) -> None:
        """Handle incoming Stripe subscription and invoice events."""
        logger.info("stripe_webhook_received", event_type=event_type)

        if event_type in ("customer.subscription.created", "customer.subscription.updated"):
            sub_id = data_object.get("id")
            cust_id = data_object.get("customer")
            status = data_object.get("status", "active")
            period_end = data_object.get("current_period_end")
            period_end_dt = datetime.fromtimestamp(period_end, tz=UTC) if period_end else None
            metadata = data_object.get("metadata", {})
            ws_id_str = metadata.get("workspace_id")

            if ws_id_str:
                try:
                    ws_id = UUID(ws_id_str)
                    sub = await self.get_or_create_subscription(ws_id)
                    sub.stripe_subscription_id = sub_id
                    sub.stripe_customer_id = cust_id
                    sub.status = (
                        SubscriptionStatus.ACTIVE
                        if status == "active"
                        else SubscriptionStatus.PAST_DUE
                    )
                    sub.current_period_end = period_end_dt
                    tier_str = metadata.get("tier", "pro")
                    sub.tier = PlanTier.PRO if tier_str == "pro" else PlanTier.ENTERPRISE
                    await self._sub_repo.update(sub)
                except Exception as exc:
                    logger.error("stripe_webhook_sub_update_failed", error=str(exc))

        elif event_type == "customer.subscription.deleted":
            sub_id = data_object.get("id")
            if sub_id:
                existing_sub = await self._sub_repo.get_by_stripe_subscription_id(sub_id)
                if existing_sub:
                    existing_sub.tier = PlanTier.FREE
                    existing_sub.status = SubscriptionStatus.CANCELED
                    await self._sub_repo.update(existing_sub)
