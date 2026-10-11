"""Test configuration and async client fixture."""

from __future__ import annotations

import os

# Must be set before src.config.settings is imported: tests exercise the mock
# bearer-token path (mock_*), which is disabled by default so it can never be
# used outside explicitly-enabled development/test environments.
os.environ.setdefault("ALLOW_DEV_TOKENS", "true")

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.infrastructure.database.session import get_db
from src.main import app


_MOCK_VAULT: dict[str, Any] = {}


class MockScalars:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class MockResult:
    def __init__(self, rows=None, rowcount=1):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._rows[0][0] if self._rows and self._rows[0] else None

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        row = self._rows[0]
        if isinstance(row, (tuple, list)):
            return row[0] if row else None
        return row

    def scalars(self):
        items = []
        for r in self._rows:
            if isinstance(r, (tuple, list)):
                items.append(r[0] if r else None)
            else:
                items.append(r)
        return MockScalars(items)


class MockAsyncSession:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()

    def add(self, instance):
        if hasattr(instance, "__tablename__") and instance.__tablename__ == "congruence_vault_secrets":
            _MOCK_VAULT[instance.id] = instance

    async def execute(self, statement, params=None):
        stmt_str = str(statement)
        if "congruence_vault_secrets" in stmt_str:
            if "DELETE" in stmt_str:
                count = len(_MOCK_VAULT)
                _MOCK_VAULT.clear()
                return MockResult([], rowcount=max(count, 1))
            return MockResult(list(_MOCK_VAULT.values()))
        if "congruence_lanes" in stmt_str:
            if "lane_unknown" in stmt_str:
                return MockResult([])
            from src.contexts.congruence.models import Lane
            return MockResult([Lane(id="lane_x", host_id="host_x", name="lane_x", slug="lane-x", worktree_path="/tmp", branch_name="main")])
        if "congruence_hosts" in stmt_str:
            from src.contexts.congruence.models import Host
            return MockResult([Host(id="host_x", project_id="proj_x", state="awake")])
        if "congruence_github_accounts" in stmt_str:
            return MockResult([])
        if "subscription.subscriptions" in stmt_str:
            return MockResult([("sub_test_123", "PRO", "active", None, None)])
        if "workspace.memberships" in stmt_str and "SELECT r.name, r.permissions" in stmt_str:
            return MockResult([("OWNER", ["*"])])
        if "workspace.memberships" in stmt_str or "workspace.workspaces" in stmt_str:
            return MockResult(
                [
                    (
                        "ws_test_123",
                        "Demo Workspace",
                        "demo-workspace",
                        "FREE",
                        datetime.now(UTC),
                        "OWNER",
                    )
                ]
            )
        return MockResult([])


    async def begin_nested(self):
        class Nested:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        return Nested()


async def override_get_db() -> AsyncIterator[MockAsyncSession]:
    yield MockAsyncSession()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
