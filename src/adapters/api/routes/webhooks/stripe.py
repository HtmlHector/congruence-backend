"""Stripe signature-verified webhook endpoint."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.billing.application.service import BillingService
from src.infrastructure.billing.stripe import stripe_service
from src.infrastructure.database.session import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks/stripe", tags=["Webhooks"])


@router.post("", status_code=status.HTTP_200_OK)
async def handle_stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
    stripe_signature: str | None = Header(None, alias="stripe-signature"),
) -> dict[str, Any]:
    """Verify and process incoming Stripe subscription and invoice events."""
    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing stripe-signature header",
        )

    body = await request.body()
    try:
        event = stripe_service.construct_webhook_event(body, stripe_signature)
    except Exception as exc:
        logger.warning("stripe_webhook_verification_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe webhook signature",
        )

    event_type = str(event.get("type", "")) if isinstance(event, dict) else str(event.type)
    event_data = (
        event.get("data", {}).get("object", {})
        if isinstance(event, dict)
        else getattr(event.data, "object", {})
    )

    billing_svc = BillingService(session)
    await billing_svc.handle_stripe_webhook(event_type, event_data)

    return {"status": "success", "event": event_type}
