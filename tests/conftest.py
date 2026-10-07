"""Test configuration and async client fixture."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.infrastructure.database.session import get_db
from src.main import app


class MockResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._rows[0][0] if self._rows and self._rows[0] else None


class MockAsyncSession:
    def __init__(self):
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, statement, params=None):
        stmt_str = str(statement)
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
        return MockResult([("1",)])

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
