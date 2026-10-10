"""Billing endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_billing_subscription_default(client: AsyncClient) -> None:
    """Test getting default subscription returns FREE tier."""
    res = await client.get("/api/v1/billing/subscription")
    assert res.status_code == 200
    data = res.json()
    assert data["tier"] == "free"
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_create_checkout_session(client: AsyncClient) -> None:
    """Test creating Stripe checkout session in dev mode."""
    # First create workspace to attach billing
    ws_res = await client.post("/api/v1/workspaces", json={"name": "Billing Test Org"})
    assert ws_res.status_code == 201

    res = await client.post(
        "/api/v1/billing/checkout",
        json={
            "tier": "pro",
            "success_url": "http://localhost:3000/billing/success",
            "cancel_url": "http://localhost:3000/billing/cancel",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "url" in data
