"""User repository implementation using async SQLAlchemy."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.identity.domain.entities import User
from src.contexts.identity.infrastructure.models import UserModel
from src.shared.repository import AbstractRepository


class UserRepository(AbstractRepository[User]):
    """SQLAlchemy implementation of user persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, model: UserModel) -> User:
        user = User(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            clerk_id=model.clerk_id,
            email=model.email,
            first_name=model.first_name,
            last_name=model.last_name,
            avatar_url=model.avatar_url,
            is_active=model.is_active,
        )
        return user

    async def get_by_id(self, entity_id: UUID) -> User | None:
        model = await self._session.get(UserModel, entity_id)
        return self._to_entity(model) if model else None

    async def get_by_clerk_id(self, clerk_id: str) -> User | None:
        query = select(UserModel).where(UserModel.clerk_id == clerk_id)
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_email(self, email: str) -> User | None:
        query = select(UserModel).where(UserModel.email == email)
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[User]:
        query = select(UserModel).limit(limit).offset(offset)
        result = await self._session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def add(self, entity: User) -> User:
        model = UserModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            clerk_id=entity.clerk_id,
            email=entity.email,
            first_name=entity.first_name,
            last_name=entity.last_name,
            avatar_url=entity.avatar_url,
            is_active=entity.is_active,
        )
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def update(self, entity: User) -> User:
        model = await self._session.get(UserModel, entity.id)
        if not model:
            raise ValueError(f"User with id {entity.id} does not exist")
        model.first_name = entity.first_name
        model.last_name = entity.last_name
        model.avatar_url = entity.avatar_url
        model.is_active = entity.is_active
        model.updated_at = entity.updated_at
        await self._session.flush()
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        model = await self._session.get(UserModel, entity_id)
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True
