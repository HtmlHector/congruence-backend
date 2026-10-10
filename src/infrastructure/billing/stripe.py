"""Stripe billing primitive — checkout sessions, customer portal, and webhooks."""

from __future__ import annotations

from typing import Any

import stripe
import structlog

from src.config.settings import settings

logger = structlog.get_logger(__name__)


class StripeService:
    """Stripe billing integration service."""

    def __init__(self, secret_key: str | None = None, webhook_secret: str | None = None) -> None:
        self._secret_key = secret_key or settings.stripe.secret_key
        self._webhook_secret = webhook_secret or settings.stripe.webhook_secret
        stripe.api_key = self._secret_key

    def create_customer(
        self,
        email: str,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Create or get a customer ID in Stripe."""
        if self._secret_key.startswith("sk_test_placeholder"):
            return f"cus_dev_{hash(email) % 1000000}"

        kwargs: dict[str, Any] = {
            "email": email,
            "metadata": metadata or {},
        }
        if name:
            kwargs["name"] = name

        customer = stripe.Customer.create(**kwargs)
        return str(customer.id)

    def create_checkout_session(
        self,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Create a hosted checkout session URL for purchasing subscriptions."""
        if self._secret_key.startswith("sk_test_placeholder"):
            return {
                "id": "cs_test_mock_123",
                "url": f"{success_url}?session_id=cs_test_mock_123",
            }

        session = stripe.checkout.Session.create(
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata or {},
        )
        return {"id": str(session.id), "url": str(session.url)}

    def create_portal_session(self, customer_id: str, return_url: str) -> dict[str, str]:
        """Create a billing portal session URL where customer manages subscriptions."""
        if self._secret_key.startswith("sk_test_placeholder"):
            return {"url": return_url}

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return {"url": str(session.url)}

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> Any:
        """Validate signature and deserialize Stripe webhook payload."""
        if (
            self._webhook_secret.startswith("whsec_placeholder")
            and settings.app.environment == "development"
        ):
            import json

            data = json.loads(payload.decode("utf-8"))
            return data

        return stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=self._webhook_secret,
        )


stripe_service = StripeService()
