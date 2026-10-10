"""Projects sample feature tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_project_crud_lifecycle(client: AsyncClient) -> None:
    """Test full CRUD lifecycle for projects in active workspace."""
    # 1. Create project
    create_res = await client.post(
        "/api/v1/projects",
        json={
            "name": "Alpha Prototype",
            "description": "Initial test project",
            "metadata": {"version": 1},
        },
    )
    assert create_res.status_code == 201
    project = create_res.json()
    project_id = project["id"]
    assert project["name"] == "Alpha Prototype"
    assert project["status"] == "active"
    assert project["metadata"]["version"] == 1

    # 2. Get project by ID
    get_res = await client.get(f"/api/v1/projects/{project_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == project_id

    # 3. List projects
    list_res = await client.get("/api/v1/projects")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) >= 1
    assert any(p["id"] == project_id for p in items)

    # 4. Update project
    patch_res = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "Alpha Prototype v2", "status": "archived"},
    )
    assert patch_res.status_code == 200
    updated = patch_res.json()
    assert updated["name"] == "Alpha Prototype v2"
    assert updated["status"] == "archived"

    # 5. Delete project
    del_res = await client.delete(f"/api/v1/projects/{project_id}")
    assert del_res.status_code == 204

    # 6. Verify 404 after deletion
    verify_res = await client.get(f"/api/v1/projects/{project_id}")
    assert verify_res.status_code == 404


@pytest.mark.asyncio
async def test_project_not_found(client: AsyncClient) -> None:
    """Test requesting a nonexistent project returns 404."""
    random_id = str(uuid4())
    res = await client.get(f"/api/v1/projects/{random_id}")
    assert res.status_code == 404
