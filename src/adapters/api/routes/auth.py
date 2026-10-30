"""Authentication endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.api.dependencies import get_current_user
from src.contexts.identity.domain.entities import User
from src.contexts.workspace.application.service import WorkspaceService
from src.infrastructure.database.session import get_db

router = APIRouter(prefix="/auth", tags=["Auth"])


class UserProfileResponse(BaseModel):
    id: str
    clerk_id: str
    email: str
    first_name: str | None
    last_name: str | None
    full_name: str
    avatar_url: str | None
    is_active: bool
    last_workspace_id: str | None
    onboarding_completed: bool


@router.get("/me", response_model=UserProfileResponse, summary="Get current user profile")
async def get_me(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Any:
    """Return verified user profile, automatically provisioned via Clerk token."""
    workspaces = await WorkspaceService(session).list_user_workspaces(user.id)
    return {
        "id": str(user.id),
        "clerk_id": user.clerk_id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
        # Most recent membership, used by the client to resolve a landing workspace.
        "last_workspace_id": str(workspaces[0].id) if workspaces else None,
        # There is no separate profile-completion record, so owning a workspace is
        # what marks setup as done for the client's onboarding gate.
        "onboarding_completed": bool(workspaces),
    }
