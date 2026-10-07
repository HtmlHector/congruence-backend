"""Stripe API client wrapper for checkout sessions, portal, and subscriptions."""

from __future__ import annotations

from typing import Any

import structlog

import stripe
from src.config.settings import settings
from stripe import StripeError

logger = structlog.get_logger(__name__)


class StripeClient:
    """Async wrapper for Stripe SDK operations."""

    def __init__(self) -> None:
        stripe.api_key = settings.stripe.secret_key.get_secret_value()

    async def get_or_create_customer(
        self,
        email: str,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> stripe.Customer:
        """Find or create a Stripe Customer by email."""
        try:
            customers = await stripe.Customer.list_async(email=email, limit=1)
            if customers.data:
                return customers.data[0]
            return await stripe.Customer.create_async(
                email=email,
                name=name or email.split("@")[0],
                metadata=metadata or {},
            )
        except StripeError as exc:
            logger.error("stripe_customer_operation_failed", error=str(exc))
            raise

    async def create_checkout_session(
        self,
        *,
        customer_email: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, Any],
    ) -> stripe.checkout.Session:
        """Create a Stripe Checkout Session for subscription purchase."""
        try:
            return await stripe.checkout.Session.create_async(
                customer_email=customer_email,
                mode="subscription",
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata=metadata,
                subscription_data={"metadata": metadata},
                allow_promotion_codes=True,
            )
        except StripeError as exc:
            logger.error("stripe_checkout_create_failed", error=str(exc))
            raise

    async def create_portal_session(
        self,
        *,
        customer_id: str,
        return_url: str,
    ) -> stripe.billing_portal.Session:
        """Create a Customer Portal Session for billing management."""
        try:
            return await stripe.billing_portal.Session.create_async(
                customer=customer_id,
                return_url=return_url,
            )
        except StripeError as exc:
            logger.error("stripe_portal_create_failed", error=str(exc))
            raise

    async def cancel_subscription(self, subscription_id: str) -> stripe.Subscription:
        """Cancel an active subscription at period end."""
        try:
            return await stripe.Subscription.modify_async(
                subscription_id,
                cancel_at_period_end=True,
            )
        except StripeError as exc:
            logger.error("stripe_subscription_cancel_failed", error=str(exc))
            raise

    async def retrieve_subscription(self, subscription_id: str) -> stripe.Subscription:
        """Retrieve a Stripe subscription object."""
        try:
            return await stripe.Subscription.retrieve_async(subscription_id)
        except StripeError as exc:
            logger.error("stripe_subscription_retrieve_failed", error=str(exc))
            raise

    async def list_invoices(self, customer_id: str, limit: int = 10) -> list[stripe.Invoice]:
        """List past invoices for a customer."""
        try:
            invoices = await stripe.Invoice.list_async(customer=customer_id, limit=limit)
            return invoices.data
        except StripeError as exc:
            logger.error("stripe_invoices_list_failed", error=str(exc))
            raise
