"""Integration tests for Congruence REST APIs."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.database.base import Base
from src.infrastructure.database.session import get_db, get_db_session
from src.main import app

# In-memory async SQLite for integration tests
test_async_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSessionFactory = async_sessionmaker(test_async_engine, expire_on_commit=False)


async def override_get_test_db():
    async with TestSessionFactory() as session:
        yield session


app.dependency_overrides[get_db] = override_get_test_db
app.dependency_overrides[get_db_session] = override_get_test_db


@pytest.fixture(autouse=True)
async def prepare_database():
    congruence_tables = [t for t in Base.metadata.sorted_tables if t.name.startswith("congruence_")]
    async with test_async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=congruence_tables))
    yield
    async with test_async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=congruence_tables))


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

        # Store BYO Anthropic Key in Project Vault
        s_resp = await client.post(
            f"/api/v1/projects/{project_id}/secrets",
            json={
                "key_name": "ANTHROPIC_API_KEY",
                "plaintext_value": "sk-ant-test-super-secret-key",
                "injection_path": "$HOME/.claude.json",
            },
        )
        assert s_resp.status_code == 201
        secret_data = s_resp.json()
        assert secret_data["status"] == "encrypted_and_stored"
