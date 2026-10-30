"""Workspace endpoint tests."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from uuid6 import uuid7

from src.adapters.api.dependencies import get_active_tenant
from src.contexts.workspace.domain.entities import WorkspaceRole
from src.contexts.workspace.infrastructure.models import WorkspaceMemberModel, WorkspaceModel
from src.infrastructure.auth.clerk import ClerkClaims

TEST_USER_UUID = UUID("00000000-0000-0000-0000-000000000002")
TEST_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


@pytest.mark.asyncio
async def test_create_and_list_workspaces(client: AsyncClient) -> None:
    """Test creating a workspace and listing workspaces."""
    create_res = await client.post("/api/v1/workspaces", json={"name": "Acme Innovations"})
    assert create_res.status_code == 201
    created = create_res.json()
    assert created["name"] == "Acme Innovations"
    assert created["slug"] == "acme-innovations"
    assert created["plan_tier"] == "free"
    assert created["created_at"]

    list_res = await client.get("/api/v1/workspaces")
    assert list_res.status_code == 200
    workspaces = list_res.json()
    assert len(workspaces) >= 1
    assert any(w["name"] == "Acme Innovations" for w in workspaces)


@pytest.mark.asyncio
async def test_tenant_rejects_unknown_workspace(
    db_session: AsyncSession, mock_claims: ClerkClaims
) -> None:
    """A client-supplied header for a deleted/unknown workspace must not reach SQL."""
    with pytest.raises(HTTPException) as exc:
        await get_active_tenant(
            request=_request(),
            claims=mock_claims,
            session=db_session,
            x_workspace_id=str(uuid7()),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_tenant_rejects_workspace_without_membership(
    db_session: AsyncSession, mock_claims: ClerkClaims
) -> None:
    """Existence is not enough; the caller must be a member."""
    workspace_id = uuid7()
    db_session.add(WorkspaceModel(id=workspace_id, name="Not Mine", slug="not-mine"))
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await get_active_tenant(
            request=_request(),
            claims=mock_claims,
            session=db_session,
            x_workspace_id=str(workspace_id),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_tenant_requires_a_workspace_header(db_session: AsyncSession) -> None:
    """Without a header or org claim there is no workspace to bind."""
    claims = ClerkClaims(user_id="clerk_test_user_1", org_id=None)
    with pytest.raises(HTTPException) as exc:
        await get_active_tenant(
            request=_request(), claims=claims, session=db_session, x_workspace_id=None
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_tenant_binds_member_workspace(
    db_session: AsyncSession, mock_claims: ClerkClaims
) -> None:
    tenant = await get_active_tenant(
        request=_request(),
        claims=mock_claims,
        session=db_session,
        x_workspace_id=TEST_WORKSPACE_ID,
    )
    assert tenant.workspace_id == TEST_WORKSPACE_ID
    assert tenant.role == "owner"


@pytest.mark.asyncio
async def test_get_update_and_delete_workspace(client: AsyncClient) -> None:
    """Detail, rename and delete round-trip for an owned workspace."""
    created = (await client.post("/api/v1/workspaces", json={"name": "Detail Co"})).json()
    workspace_id = created["id"]

    detail = await client.get(f"/api/v1/workspaces/{workspace_id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["id"] == workspace_id
    assert payload["name"] == "Detail Co"
    assert payload["slug"] == "detail-co"
    assert payload["plan_tier"] == "free"

    renamed = await client.patch(f"/api/v1/workspaces/{workspace_id}", json={"name": "Renamed Co"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Renamed Co"
    assert renamed.json()["slug"] == "detail-co"

    empty_patch = await client.patch(f"/api/v1/workspaces/{workspace_id}", json={})
    assert empty_patch.status_code == 422

    deleted = await client.delete(f"/api/v1/workspaces/{workspace_id}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/workspaces/{workspace_id}")).status_code == 404
    assert all(w["id"] != workspace_id for w in (await client.get("/api/v1/workspaces")).json())


@pytest.mark.asyncio
async def test_workspace_is_hidden_from_non_members(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A workspace the caller does not belong to behaves as if it does not exist."""
    workspace_id = uuid7()
    db_session.add(WorkspaceModel(id=workspace_id, name="Someone Else", slug="someone-else"))
    await db_session.commit()

    assert (await client.get(f"/api/v1/workspaces/{workspace_id}")).status_code == 404
    assert (
        await client.patch(f"/api/v1/workspaces/{workspace_id}", json={"name": "Nope"})
    ).status_code == 404
    assert (await client.delete(f"/api/v1/workspaces/{workspace_id}")).status_code == 404


@pytest.mark.asyncio
async def test_member_role_cannot_rename_or_delete(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Members can read a workspace but not mutate it."""
    workspace_id = uuid7()
    db_session.add(WorkspaceModel(id=workspace_id, name="Team Space", slug="team-space"))
    db_session.add(
        WorkspaceMemberModel(
            workspace_id=workspace_id,
            user_id=TEST_USER_UUID,
            role=WorkspaceRole.MEMBER,
        )
    )
    await db_session.commit()

    assert (await client.get(f"/api/v1/workspaces/{workspace_id}")).status_code == 200
    assert (
        await client.patch(f"/api/v1/workspaces/{workspace_id}", json={"name": "Nope"})
    ).status_code == 403
    assert (await client.delete(f"/api/v1/workspaces/{workspace_id}")).status_code == 403
