"""Tests for GitHub webhook event processing and branch/PR synchronization."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.auth.dependencies import get_current_user
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.database.base import Base
from src.infrastructure.database.session import get_db, get_db_session
from src.main import app

test_async_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSessionFactory = async_sessionmaker(test_async_engine, expire_on_commit=False)

TEST_USER = UserClaims(user_id="usr_owner", email="owner@example.com")


async def override_get_test_db():
    async with TestSessionFactory() as session:
        yield session


def override_get_current_user() -> UserClaims:
    return TEST_USER


@pytest.fixture(autouse=True)
async def prepare_database():
    orig_get_db = app.dependency_overrides.get(get_db)
    orig_get_db_session = app.dependency_overrides.get(get_db_session)
    orig_get_user = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_db] = override_get_test_db
    app.dependency_overrides[get_db_session] = override_get_test_db
    app.dependency_overrides[get_current_user] = override_get_current_user

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

    if orig_get_user is not None:
        app.dependency_overrides[get_current_user] = orig_get_user
    else:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_github_webhook_ping():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/integrations/github/webhooks",
            headers={"X-GitHub-Event": "ping"},
            json={"zen": "Non-blocking is better than blocking."},
        )
        assert resp.status_code == 200
        assert resp.json()["event"] == "ping"


@pytest.mark.asyncio
async def test_github_webhook_pr_sync_and_push():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create Project
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "Webhook Test App",
                "slug": "webhook-test-app",
                "repo_full_name": "owner/webhook-app",
                "default_branch": "main",
                "visibility": "private",
            },
        )
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        # Create Agent Lane with branch
        lane_resp = await client.post(
            f"/api/v1/projects/{project_id}/lanes",
            json={
                "name": "Claude Agent Lane",
                "slug": "claude-progress",
                "branch_name": "agent/claude-progress",
            },
        )
        assert lane_resp.status_code == 201

        # 1. Send Pull Request Opened Webhook
        pr_payload = {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "html_url": "https://github.com/owner/webhook-app/pull/42",
                "head": {"ref": "agent/claude-progress"},
                "merged": False,
            },
        }
        webhook_pr_resp = await client.post(
            "/api/v1/integrations/github/webhooks",
            headers={"X-GitHub-Event": "pull_request"},
            json=pr_payload,
        )
        assert webhook_pr_resp.status_code == 200
        assert webhook_pr_resp.json()["pr_number"] == 42
        assert webhook_pr_resp.json()["pr_status"] == "open"

        # Verify PR shows up in project pull-requests endpoint
        prs_resp = await client.get(f"/api/v1/projects/{project_id}/github/pull-requests")
        assert prs_resp.status_code == 200
        prs = prs_resp.json()
        assert len(prs) == 1
        assert prs[0]["pr_number"] == 42
        assert prs[0]["status"] == "open"

        # 2. Send Pull Request Merged Webhook
        pr_merged_payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "html_url": "https://github.com/owner/webhook-app/pull/42",
                "head": {"ref": "agent/claude-progress"},
                "merged": True,
            },
        }
        merged_resp = await client.post(
            "/api/v1/integrations/github/webhooks",
            headers={"X-GitHub-Event": "pull_request"},
            json=pr_merged_payload,
        )
        assert merged_resp.status_code == 200
        assert merged_resp.json()["pr_status"] == "merged"

        # 3. Send Push Webhook
        push_payload = {
            "ref": "refs/heads/agent/claude-progress",
            "head_commit": {
                "id": "a1b2c3d4e5f67890",
                "message": "feat: add feature in agent lane",
            },
        }
        push_resp = await client.post(
            "/api/v1/integrations/github/webhooks",
            headers={"X-GitHub-Event": "push"},
            json=push_payload,
        )
        assert push_resp.status_code == 200
        assert push_resp.json()["event"] == "push"

        # Verify activity feed recorded the webhook events
        activity_resp = await client.get(f"/api/v1/projects/{project_id}/activity")
        assert activity_resp.status_code == 200
        events = activity_resp.json()
        action_types = [e["action_type"] for e in events]
        assert "github_pr_opened" in action_types
        assert "github_pr_closed" in action_types
        assert "github_push" in action_types
