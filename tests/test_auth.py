"""Authentication endpoint tests."""

from __future__ import annotations

import base64

import pytest
from httpx import AsyncClient

from src.infrastructure.auth.clerk import derive_jwks_url


@pytest.mark.asyncio
async def test_auth_me(client: AsyncClient) -> None:
    """Test /api/v1/auth/me returns authenticated user details."""
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@parabox.so"
    assert data["first_name"] == "Test"
    assert data["last_name"] == "User"
    assert data["is_active"] is True
    # The seeded membership is the client's landing workspace.
    assert data["last_workspace_id"] == "00000000-0000-0000-0000-000000000001"
    assert data["onboarding_completed"] is True


@pytest.mark.asyncio
async def test_new_user_is_provisioned_with_personal_workspace(db_session, monkeypatch) -> None:
    """First sign-in creates the user and a workspace they own."""
    from src.contexts.identity.application.service import IdentityService
    from src.contexts.workspace.infrastructure.repository import WorkspaceRepository
    from src.infrastructure.auth.clerk import ClerkClaims, clerk_service

    async def fake_get_user(clerk_user_id: str) -> dict:
        return {
            "id": clerk_user_id,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email_addresses": [{"email_address": "ada@example.com"}],
            "image_url": None,
        }

    monkeypatch.setattr(clerk_service, "get_user", fake_get_user)

    claims = ClerkClaims(user_id="clerk_brand_new_user", email="ada@example.com")
    user = await IdentityService(db_session).get_or_provision_user(claims)

    workspaces = await WorkspaceRepository(db_session).list_for_user(user.id)
    assert [w.name for w in workspaces] == ["Personal"]


@pytest.mark.asyncio
async def test_existing_user_without_workspace_is_not_reseeded(
    client: AsyncClient, db_session
) -> None:
    """Only first sign-in provisions a workspace; deleting them all is not undone."""
    from sqlalchemy import delete

    from src.contexts.workspace.infrastructure.models import WorkspaceMemberModel

    await db_session.execute(delete(WorkspaceMemberModel))
    await db_session.commit()

    data = (await client.get("/api/v1/auth/me")).json()
    assert data["last_workspace_id"] is None


def test_derive_jwks_url_from_publishable_key() -> None:
    """Clerk publishes the frontend API host inside the publishable key."""
    host = "clerk.example-tenant.dev"
    encoded = base64.b64encode(f"{host}$".encode()).decode().rstrip("=")
    assert derive_jwks_url(f"pk_test_{encoded}") == f"https://{host}/.well-known/jwks.json"

    legacy = base64.b64encode(host.encode()).decode().rstrip("=")
    assert derive_jwks_url(f"pk_live_{legacy}") == f"https://{host}/.well-known/jwks.json"


def test_derive_jwks_url_rejects_placeholders() -> None:
    assert derive_jwks_url(None) is None
    assert derive_jwks_url("") is None
    assert derive_jwks_url("pk_test_placeholder") is None
    assert derive_jwks_url("not-a-clerk-key") is None
