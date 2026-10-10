"""Workspace endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_and_list_workspaces(client: AsyncClient) -> None:
    """Test creating a workspace and listing workspaces."""
    create_res = await client.post("/api/v1/workspaces", json={"name": "Acme Innovations"})
    assert create_res.status_code == 201
    created = create_res.json()
    assert created["name"] == "Acme Innovations"
    assert created["slug"] == "acme-innovations"

    list_res = await client.get("/api/v1/workspaces")
    assert list_res.status_code == 200
    workspaces = list_res.json()
    assert len(workspaces) >= 1
    assert any(w["name"] == "Acme Innovations" for w in workspaces)
