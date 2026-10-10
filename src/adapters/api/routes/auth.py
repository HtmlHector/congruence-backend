"""Authentication endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.adapters.api.dependencies import get_current_user
from src.contexts.identity.domain.entities import User

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


@router.get("/me", response_model=UserProfileResponse, summary="Get current user profile")
async def get_me(user: User = Depends(get_current_user)) -> Any:
    """Return verified user profile, automatically provisioned via Clerk token."""
    return {
        "id": str(user.id),
        "clerk_id": user.clerk_id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
    }
