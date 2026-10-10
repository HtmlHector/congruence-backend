"""Billing and subscription endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_active_tenant
from src.contexts.billing.application.service import BillingService
from src.contexts.billing.domain.entities import PlanTier
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

router = APIRouter(prefix="/billing", tags=["Billing"])


class CheckoutSessionRequest(BaseModel):
    tier: PlanTier = PlanTier.PRO
    success_url: str
    cancel_url: str


class PortalSessionRequest(BaseModel):
    return_url: str


@router.get("/subscription")
async def get_subscription(
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Fetch active subscription tier and billing status for active workspace."""
    if not tenant.workspace_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace")

    billing_svc = BillingService(session)
    sub = await billing_svc.get_or_create_subscription(tenant.workspace_uuid)
    return {
        "id": str(sub.id),
        "workspace_id": str(sub.workspace_id),
        "tier": sub.tier.value,
        "status": sub.status.value,
        "current_period_end": sub.current_period_end,
        "cancel_at_period_end": sub.cancel_at_period_end,
    }


@router.post("/checkout")
async def create_checkout_session(
    payload: CheckoutSessionRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Create hosted Stripe Checkout Session for subscription upgrade."""
    if not tenant.workspace_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace")

    billing_svc = BillingService(session)
    try:
        res = await billing_svc.create_checkout_session(
            workspace_id=tenant.workspace_uuid,
            tier=payload.tier,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
        )
        return res
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/portal")
async def create_portal_session(
    payload: PortalSessionRequest,
    tenant: TenantContext = Depends(get_active_tenant),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Create Stripe Customer Portal session."""
    if not tenant.workspace_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active workspace")

    billing_svc = BillingService(session)
    try:
        return await billing_svc.create_portal_session(
            workspace_id=tenant.workspace_uuid,
            return_url=payload.return_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
