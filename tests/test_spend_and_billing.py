"""Tests for compute spend tracking, egress limits, and Stripe writer seat billing."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.database.base import Base
from src.infrastructure.database.session import get_db, get_db_session
from src.main import app

test_async_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSessionFactory = async_sessionmaker(test_async_engine, expire_on_commit=False)


async def override_get_test_db():
    async with TestSessionFactory() as session:
        yield session


@pytest.fixture(autouse=True)
async def prepare_database():
    orig_get_db = app.dependency_overrides.get(get_db)
    orig_get_db_session = app.dependency_overrides.get(get_db_session)
    app.dependency_overrides[get_db] = override_get_test_db
    app.dependency_overrides[get_db_session] = override_get_test_db

    congruence_tables = [t for t in Base.metadata.sorted_tables if t.name.startswith("congruence_")]
    async with test_async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=congruence_tables))
    yield
    async with test_async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=congruence_tables))

    if orig_get_db is not None:
        app.dependency_overrides[get_db] = orig_get_db
    else:
        app.dependency_overrides.pop(get_db, None)

    if orig_get_db_session is not None:
        app.dependency_overrides[get_db_session] = orig_get_db_session
    else:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_spend_calculation_and_caps():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create Project
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "Compute Spend Project",
                "slug": "compute-spend-project",
                "repo_full_name": "owner/compute-app",
                "default_branch": "main",
                "visibility": "private",
            },
        )
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        # 1. Fetch Real-time Compute Spend
        spend_resp = await client.get(f"/api/v1/projects/{project_id}/spend")
        assert spend_resp.status_code == 200
        spend_data = spend_resp.json()
        assert spend_data["project_id"] == project_id
        assert spend_data["host_state"] == "awake"
        assert "cost_breakdown_usd" in spend_data
        assert "cpu" in spend_data["cost_breakdown_usd"]
        assert "memory" in spend_data["cost_breakdown_usd"]
        assert "disk" in spend_data["cost_breakdown_usd"]
        assert spend_data["spend_cap_usd"] is None
        assert spend_data["spend_cap_reached"] is False

        # 2. Update Spend Caps and Egress Limits
        caps_resp = await client.put(
            f"/api/v1/projects/{project_id}/spend-caps",
            json={
                "spend_cap_usd": 25.0,
                "egress_limit_mb": 5000,
                "auto_idle_minutes": 15,
            },
        )
        assert caps_resp.status_code == 200
        assert caps_resp.json()["spend_cap_usd"] == 25.0
        assert caps_resp.json()["egress_limit_mb"] == 5000
        assert caps_resp.json()["auto_idle_minutes"] == 15

        # 3. Verify Spend Caps Updated
        spend_resp2 = await client.get(f"/api/v1/projects/{project_id}/spend")
        assert spend_resp2.status_code == 200
        assert spend_resp2.json()["spend_cap_usd"] == 25.0
        assert spend_resp2.json()["egress_limit_mb"] == 5000
        assert spend_resp2.json()["auto_idle_minutes"] == 15


@pytest.mark.asyncio
async def test_billing_subscription_and_checkout():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create Project
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "Billing Test Project",
                "slug": "billing-test-project",
                "repo_full_name": "owner/billing-app",
                "default_branch": "main",
                "visibility": "private",
            },
        )
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        # 1. Fetch Subscription Info
        sub_resp = await client.get(f"/api/v1/projects/{project_id}/billing/subscription")
        assert sub_resp.status_code == 200
        sub_data = sub_resp.json()
        assert sub_data["plan_tier"] == "pro"
        assert sub_data["writer_seats_licensed"] == 1
        assert sub_data["active_writers_count"] == 1  # Owner
        assert sub_data["seat_price_usd_month"] == 49.0
        assert sub_data["watchers_are_free"] is True

        # 2. Checkout Additional Writer Seats
        checkout_resp = await client.post(
            f"/api/v1/projects/{project_id}/billing/checkout-writer-seat",
            json={"quantity": 3},
        )
        assert checkout_resp.status_code == 200
        checkout_data = checkout_resp.json()
        assert checkout_data["quantity"] == 3
        assert checkout_data["unit_price_usd"] == 49.0
        assert checkout_data["total_usd"] == 147.0
        assert "checkout_url" in checkout_data
