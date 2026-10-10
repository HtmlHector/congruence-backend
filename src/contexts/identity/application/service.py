"""User identity application service."""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.identity.domain.entities import User
from src.contexts.identity.infrastructure.repository import UserRepository
from src.infrastructure.auth.clerk import ClerkClaims, clerk_service

logger = structlog.get_logger(__name__)


class IdentityService:
    """Orchestrates JIT provisioning and profile synchronization."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = UserRepository(session)

    async def get_or_provision_user(self, claims: ClerkClaims) -> User:
        """Fetch existing user by Clerk ID or JIT provision if not yet present."""
        user = await self._repo.get_by_clerk_id(claims.user_id)
        if user:
            return user

        logger.info("jit_provisioning_user", clerk_id=claims.user_id)
        profile = await clerk_service.get_user(claims.user_id)

        emails = profile.get("email_addresses", [])
        email = (
            emails[0].get("email_address")
            if emails
            else (claims.email or f"{claims.user_id}@users.clerk.dev")
        )

        user = User(
            clerk_id=claims.user_id,
            email=email,
            first_name=profile.get("first_name"),
            last_name=profile.get("last_name"),
            avatar_url=profile.get("image_url"),
        )
        return await self._repo.add(user)

    async def sync_clerk_webhook(self, event_type: str, data: dict) -> User | None:
        """Sync user state from Svix-verified Clerk webhook."""
        clerk_id = data.get("id")
        if not clerk_id:
            return None

        if event_type in ("user.created", "user.updated"):
            emails = data.get("email_addresses", [])
            email = emails[0].get("email_address") if emails else f"{clerk_id}@users.clerk.dev"

            existing = await self._repo.get_by_clerk_id(clerk_id)
            if existing:
                existing.email = email
                existing.first_name = data.get("first_name")
                existing.last_name = data.get("last_name")
                existing.avatar_url = data.get("image_url")
                return await self._repo.update(existing)
            else:
                new_user = User(
                    clerk_id=clerk_id,
                    email=email,
                    first_name=data.get("first_name"),
                    last_name=data.get("last_name"),
                    avatar_url=data.get("image_url"),
                )
                return await self._repo.add(new_user)

        elif event_type == "user.deleted":
            existing = await self._repo.get_by_clerk_id(clerk_id)
            if existing:
                await self._repo.delete(existing.id)
            return None

        return None
