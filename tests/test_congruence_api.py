"""Integration tests for Congruence REST APIs."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.auth.dependencies import get_current_user
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.database.base import Base
from src.infrastructure.database.session import get_db, get_db_session
from src.main import app

# In-memory async SQLite for integration tests
test_async_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSessionFactory = async_sessionmaker(test_async_engine, expire_on_commit=False)

OWNER = UserClaims(user_id="usr_owner", email="owner@example.com")
OUTSIDER = UserClaims(user_id="usr_outsider", email="outsider@example.com")
current_test_user: dict[str, UserClaims] = {"user": OWNER}


async def override_get_test_db():
    async with TestSessionFactory() as session:
        yield session


def override_get_current_user() -> UserClaims:
    return current_test_user["user"]


@pytest.fixture(autouse=True)
async def prepare_database():
    orig_get_db = app.dependency_overrides.get(get_db)
    orig_get_db_session = app.dependency_overrides.get(get_db_session)
    app.dependency_overrides[get_db] = override_get_test_db
    app.dependency_overrides[get_db_session] = override_get_test_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    current_test_user["user"] = OWNER

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

    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_project_routes_require_authentication():
    transport = ASGITransport(app=app)
    app.dependency_overrides.pop(get_current_user, None)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/projects")
            assert resp.status_code == 401
    finally:
        app.dependency_overrides[get_current_user] = override_get_current_user


@pytest.mark.asyncio
async def test_projects_are_scoped_to_member_orgs():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/projects",
            json={"name": "Owner Project", "slug": "owner-project", "repo_full_name": "acme/owner"},
        )
        assert created.status_code == 201
        project_id = created.json()["id"]

        listed = await client.get("/api/v1/projects")
        assert project_id in [p["id"] for p in listed.json()]
        assert (await client.get(f"/api/v1/projects/{project_id}")).status_code == 200

        current_test_user["user"] = OUTSIDER
        try:
            listed = await client.get("/api/v1/projects")
            assert listed.json() == []
            assert (await client.get(f"/api/v1/projects/{project_id}")).status_code == 404
            assert (await client.get(f"/api/v1/projects/{project_id}/lanes")).status_code == 404
            assert (await client.get(f"/api/v1/projects/{project_id}/host")).status_code == 404
        finally:
            current_test_user["user"] = OWNER


@pytest.mark.asyncio
async def test_host_usage_tracks_state_and_estimates_cost():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/projects",
            json={"name": "Usage Project", "slug": "usage-project", "repo_full_name": "acme/usage"},
        )
        assert created.status_code == 201
        project_id = created.json()["id"]

        usage = (await client.get(f"/api/v1/projects/{project_id}/host/usage")).json()
        assert usage["state"] == "awake"
        assert usage["awake_seconds_month"] >= 0
        assert usage["rate_usd_per_hour"] == 0.15
        assert usage["estimated_cost_usd_month"] >= 0

        assert (await client.post(f"/api/v1/projects/{project_id}/host/sleep")).status_code == 200
        usage = (await client.get(f"/api/v1/projects/{project_id}/host/usage")).json()
        assert usage["state"] == "asleep"

        assert (await client.post(f"/api/v1/projects/{project_id}/host/wake")).status_code == 200
        usage = (await client.get(f"/api/v1/projects/{project_id}/host/usage")).json()
        assert usage["state"] == "awake"


@pytest.mark.asyncio
async def test_create_and_fetch_project():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create Project
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "Fieldnotes App",
                "slug": "fieldnotes-app",
                "repo_full_name": "parabox/fieldnotes",
                "default_branch": "main",
                "visibility": "private",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["slug"] == "fieldnotes-app"
        assert "host_id" in data
        assert "pair_lane_id" in data
        project_id = data["id"]

        # 2. Get Project Details
        get_resp = await client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.status_code == 200
        proj_data = get_resp.json()
        assert proj_data["name"] == "Fieldnotes App"
        assert proj_data["host"]["state"] == "awake"
        assert len(proj_data["lanes"]) >= 1
        assert len(proj_data["actors"]) >= 2  # Human owner + Claude Code


@pytest.mark.asyncio
async def test_create_agent_lane_and_store_secret():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create Project
        p_resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "Analytics Dashboard",
                "slug": "analytics-dash",
                "repo_full_name": "parabox/analytics",
                "default_branch": "main",
                "visibility": "private",
            },
        )
        project_id = p_resp.json()["id"]

        # Create Lane for Codex
        l_resp = await client.post(
            f"/api/v1/projects/{project_id}/lanes",
            json={
                "name": "OpenAI Codex Lane",
                "slug": "codex-lane",
                "branch_name": "agent/codex-work",
                "is_pair_lane": False,
            },
        )
        assert l_resp.status_code == 201
        lane_data = l_resp.json()
        assert lane_data["slug"] == "codex-lane"
        assert lane_data["branch_name"] == "agent/codex-work"

        # Test Grant Control for Lane
        g_resp = await client.post(
            f"/api/v1/projects/{project_id}/lanes/{lane_data['id']}/grant",
            json={
                "actor_id": "act_claude",
                "permission": "write",
            },
        )
        assert g_resp.status_code == 200
        grant_data = g_resp.json()
        assert grant_data["permission"] == "write"
        assert grant_data["actor_id"] == "act_claude"

        # Test Host Sleep & Wake
        sleep_resp = await client.post(f"/api/v1/projects/{project_id}/host/sleep")
        assert sleep_resp.status_code == 200
        assert sleep_resp.json()["status"] == "asleep"

        wake_resp = await client.post(f"/api/v1/projects/{project_id}/host/wake")
        assert wake_resp.status_code == 200
        assert wake_resp.json()["status"] == "awake"

        # Test Services
        srv_resp = await client.get(f"/api/v1/projects/{project_id}/services")
        assert srv_resp.status_code == 200
        assert isinstance(srv_resp.json(), list)

        # Test Git Diff
        diff_resp = await client.get(f"/api/v1/projects/{project_id}/git/diff?lane_id={lane_data['id']}")
        assert diff_resp.status_code == 200
        assert "files_changed" in diff_resp.json()

        # Test Pull Requests
        pr_resp = await client.get(f"/api/v1/projects/{project_id}/github/pull-requests")
        assert pr_resp.status_code == 200
        assert isinstance(pr_resp.json(), list)

        # Test Activity Feed
        act_resp = await client.get(f"/api/v1/projects/{project_id}/activity")
        assert act_resp.status_code == 200
        events = act_resp.json()
        assert len(events) >= 3  # project_created, lane_created, grant_issued, etc.
