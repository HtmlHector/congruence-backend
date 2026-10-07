"""FastAPI router for Stripe checkout, customer portal, invoices, and webhooks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import stripe
from src.config.settings import settings
from src.infrastructure.auth.dependencies import (
    get_current_user,
    require_permission,
)
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.database.session import get_db
from src.infrastructure.security.audit import AuditLogger
from src.infrastructure.stripe.client import StripeClient

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    workspace_id: str
    price_id: str
    success_url: str
    cancel_url: str


class PortalRequest(BaseModel):
    workspace_id: str
    return_url: str


@router.post("/checkout")
async def create_checkout(
    req: CheckoutRequest,
    user: UserClaims = Depends(get_current_user),
    membership: dict = Depends(require_permission("billing:write")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Initiate a Stripe Checkout session for a workspace."""
    ws_row = (
        await db.execute(
            text(
                "SELECT workspace_id, name, stripe_customer_id FROM workspace.workspaces WHERE workspace_id = :ws_id"
            ),
            {"ws_id": req.workspace_id},
        )
    ).fetchone()

    if not ws_row:
        raise HTTPException(status_code=404, detail="Workspace not found")

    client = StripeClient()
    session = await client.create_checkout_session(
        customer_email=user.email,
        price_id=req.price_id,
        success_url=req.success_url,
        cancel_url=req.cancel_url,
        metadata={
            "workspace_id": req.workspace_id,
            "user_id": user.user_id,
        },
    )
    return {"checkout_url": session.url}


@router.post("/portal")
async def create_portal(
    req: PortalRequest,
    user: UserClaims = Depends(get_current_user),
    membership: dict = Depends(require_permission("billing:write")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Generate a Stripe billing portal URL for managing payment methods & invoices."""
    ws_row = (
        await db.execute(
            text("SELECT stripe_customer_id FROM workspace.workspaces WHERE workspace_id = :ws_id"),
            {"ws_id": req.workspace_id},
        )
    ).fetchone()

    if not ws_row or not ws_row[0]:
        raise HTTPException(status_code=400, detail="No active Stripe customer found for workspace")

    client = StripeClient()
    portal = await client.create_portal_session(
        customer_id=ws_row[0],
        return_url=req.return_url,
    )
    return {"portal_url": portal.url}


@router.get("/workspaces/{workspace_id}")
async def get_workspace_billing(
    workspace_id: str,
    membership: dict = Depends(require_permission("billing:read")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve billing status, active subscription details, and customer information."""
    ws_row = (
        await db.execute(
            text(
                "SELECT workspace_id, name, plan_tier, stripe_customer_id FROM workspace.workspaces WHERE workspace_id = :ws_id"
            ),
            {"ws_id": workspace_id},
        )
    ).fetchone()

    if not ws_row:
        raise HTTPException(status_code=404, detail="Workspace not found")

    sub_row = (
        await db.execute(
            text(
                """
                SELECT stripe_subscription_id, plan_tier, status, current_period_end, cancelled_at
                FROM subscription.subscriptions
                WHERE workspace_id = :ws_id
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"ws_id": workspace_id},
        )
    ).fetchone()

    subscription = None
    if sub_row:
        subscription = {
            "subscription_id": sub_row[0],
            "plan_tier": sub_row[1],
            "status": sub_row[2],
            "current_period_end": sub_row[3].isoformat() if sub_row[3] else None,
            "cancelled_at": sub_row[4].isoformat() if sub_row[4] else None,
        }

    return {
        "workspace_id": ws_row[0],
        "name": ws_row[1],
        "plan_tier": ws_row[2],
        "has_payment_method": bool(ws_row[3]),
        "stripe_customer_id": ws_row[3],
        "subscription": subscription,
    }


@router.post("/workspaces/{workspace_id}/cancel")
async def cancel_workspace_subscription(
    workspace_id: str,
    membership: dict = Depends(require_permission("billing:write")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Cancel subscription at period end."""
    sub_row = (
        await db.execute(
            text(
                "SELECT stripe_subscription_id FROM subscription.subscriptions WHERE workspace_id = :ws_id AND status = 'active'"
            ),
            {"ws_id": workspace_id},
        )
    ).fetchone()

    if not sub_row:
        raise HTTPException(status_code=404, detail="No active subscription found to cancel")

    stripe_sub_id = sub_row[0]
    client = StripeClient()
    await client.cancel_subscription(stripe_sub_id)

    now = datetime.now(UTC)
    await db.execute(
        text(
            "UPDATE subscription.subscriptions SET cancelled_at = :now WHERE stripe_subscription_id = :sub_id"
        ),
        {"now": now, "sub_id": stripe_sub_id},
    )

    await AuditLogger(db).log(
        workspace_id=workspace_id,
        actor_id=membership["user_id"],
        actor_type="USER",
        action="subscription.cancelled",
        resource_type="billing",
        resource_id=stripe_sub_id,
        details={"stripe_subscription_id": stripe_sub_id},
    )

    return {"status": "cancelling_at_period_end", "stripe_subscription_id": stripe_sub_id}


@router.get("/workspaces/{workspace_id}/invoices")
async def list_workspace_invoices(
    workspace_id: str,
    membership: dict = Depends(require_permission("billing:read")),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List recent invoices from Stripe."""
    ws_row = (
        await db.execute(
            text("SELECT stripe_customer_id FROM workspace.workspaces WHERE workspace_id = :ws_id"),
            {"ws_id": workspace_id},
        )
    ).fetchone()

    if not ws_row or not ws_row[0]:
        return []

    client = StripeClient()
    invoices = await client.list_invoices(customer_id=ws_row[0])
    return [
        {
            "id": inv.id,
            "number": inv.number,
            "amount_paid": inv.amount_paid,
            "currency": inv.currency,
            "status": inv.status,
            "hosted_invoice_url": inv.hosted_invoice_url,
            "pdf": inv.invoice_pdf,
            "created": datetime.fromtimestamp(inv.created, tz=UTC).isoformat()
            if inv.created
            else None,
        }
        for inv in invoices
    ]


@router.post("/webhook")
async def handle_stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Process incoming Stripe webhooks (checkout completed, subscription status)."""
    payload = await request.body()
    webhook_secret = settings.stripe.webhook_secret.get_secret_value()

    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, webhook_secret)
    except Exception as exc:
        logger.warning("stripe_webhook_verification_failed", error=str(exc))
        raise HTTPException(status_code=400, detail="Webhook signature verification failed")

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info("stripe_webhook_received", event_type=event_type)

    if event_type == "checkout.session.completed":
        workspace_id = data.get("metadata", {}).get("workspace_id")
        customer_id = data.get("customer")
        subscription_id = data.get("subscription")

        if workspace_id and customer_id:
            await db.execute(
                text(
                    "UPDATE workspace.workspaces SET stripe_customer_id = :cust_id, plan_tier = 'PRO' WHERE workspace_id = :ws_id"
                ),
                {"cust_id": customer_id, "ws_id": workspace_id},
            )
            if subscription_id:
                await db.execute(
                    text(
                        """
                        INSERT INTO subscription.subscriptions
                        (workspace_id, stripe_subscription_id, stripe_customer_id, plan_tier, status)
                        VALUES (:ws_id, :sub_id, :cust_id, 'PRO', 'active')
                        ON CONFLICT (stripe_subscription_id) DO UPDATE SET status = 'active', plan_tier = 'PRO'
                        """
                    ),
                    {"ws_id": workspace_id, "sub_id": subscription_id, "cust_id": customer_id},
                )
            await AuditLogger(db).log(
                workspace_id=workspace_id,
                actor_id=customer_id,
                actor_type="WEBHOOK",
                action="checkout.completed",
                resource_type="billing",
                resource_id=subscription_id,
                details=data,
            )
            await db.commit()

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub_id = data.get("id")
        status = data.get("status")
        cancel_at = data.get("canceled_at")
        cancelled_at = datetime.fromtimestamp(cancel_at, tz=UTC) if cancel_at else None

        sub_row = (
            await db.execute(
                text(
                    "SELECT workspace_id FROM subscription.subscriptions WHERE stripe_subscription_id = :sub_id"
                ),
                {"sub_id": sub_id},
            )
        ).fetchone()

        await db.execute(
            text(
                "UPDATE subscription.subscriptions SET status = :status, cancelled_at = :cancel WHERE stripe_subscription_id = :sub_id"
            ),
            {"status": status, "cancel": cancelled_at, "sub_id": sub_id},
        )
        if sub_row:
            await AuditLogger(db).log(
                workspace_id=sub_row[0],
                actor_id=data.get("customer"),
                actor_type="WEBHOOK",
                action=f"subscription.{status}",
                resource_type="billing",
                resource_id=sub_id,
                details={"status": status},
            )
        await db.commit()

    return {"received": True}
