"""Audit trail endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_audit_logs_recorded_on_project_action(client: AsyncClient) -> None:
    """Test that creating a project records an audit log entry."""
    # Create project to trigger audit entry
    await client.post("/api/v1/projects", json={"name": "Audit Test Project"})

    # Fetch audit logs
    res = await client.get("/api/v1/audit")
    assert res.status_code == 200
    logs = res.json()
    assert len(logs) >= 1
    assert any(log["action"] == "project.create" for log in logs)
