"""Generic tenant-scoped async SQLAlchemy repository primitive."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.base import Base
from src.shared.base_entity import BaseEntity
from src.shared.repository import AbstractRepository

TModel = TypeVar("TModel", bound=Base)
TEntity = TypeVar("TEntity", bound=BaseEntity)


class TenantScopedRepository[TModel: Base, TEntity: BaseEntity](AbstractRepository[TEntity]):
    """Generic async repository that enforces workspace_id isolation on all queries.

    Subclasses implement:
      - `_model_cls`: The SQLAlchemy ORM model class
      - `_to_entity(model)`: Convert ORM model to Domain Entity
      - `_to_model(entity)`: Convert Domain Entity to ORM model
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    @abstractmethod
    def _model_cls(self) -> type[TModel]:
        """SQLAlchemy model class bound to this repository."""
        raise NotImplementedError

    @abstractmethod
    def _to_entity(self, model: TModel) -> TEntity:
        """Convert ORM model to domain entity."""
        raise NotImplementedError

    @abstractmethod
    def _to_model(self, entity: TEntity) -> TModel:
        """Convert domain entity to ORM model."""
        raise NotImplementedError

    async def get_by_id(self, entity_id: UUID) -> TEntity | None:
        """Fetch by primary key."""
        model = await self._session.get(self._model_cls, entity_id)
        return self._to_entity(model) if model else None

    async def get_by_id_and_workspace(
        self, entity_id: UUID, workspace_id: UUID
    ) -> TEntity | None:
        """Fetch entity strictly bounded to workspace_id."""
        query = select(self._model_cls).where(
            self._model_cls.id == entity_id,  # type: ignore[attr-defined]
            self._model_cls.workspace_id == workspace_id,  # type: ignore[attr-defined]
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_workspace(
        self,
        workspace_id: UUID,
        limit: int = 50,
        offset: int = 0,
        filters: Sequence[Any] | None = None,
    ) -> Sequence[TEntity]:
        """List entities for a workspace with optional extra filter clauses."""
        query = select(self._model_cls).where(
            self._model_cls.workspace_id == workspace_id  # type: ignore[attr-defined]
        )
        if filters:
            for clause in filters:
                query = query.where(clause)

        if hasattr(self._model_cls, "created_at"):
            query = query.order_by(desc(self._model_cls.created_at))  # type: ignore[attr-defined]

        query = query.limit(limit).offset(offset)
        result = await self._session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[TEntity]:
        """Fetch paginated list across entities."""
        query = select(self._model_cls)
        if hasattr(self._model_cls, "created_at"):
            query = query.order_by(desc(self._model_cls.created_at))  # type: ignore[attr-defined]
        query = query.limit(limit).offset(offset)
        result = await self._session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def add(self, entity: TEntity) -> TEntity:
        """Persist a new domain entity."""
        model = self._to_model(entity)
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def update(self, entity: TEntity) -> TEntity:
        """Update an existing model by matching its entity id."""
        model = await self._session.get(self._model_cls, entity.id)
        if not model:
            raise ValueError(f"{self._model_cls.__name__} with id {entity.id} does not exist")

        updated_model = self._to_model(entity)
        for attr, value in vars(updated_model).items():
            if not attr.startswith("_"):
                setattr(model, attr, value)

        await self._session.flush()
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        """Delete entity by ID."""
        model = await self._session.get(self._model_cls, entity_id)
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True

    async def delete_for_workspace(self, entity_id: UUID, workspace_id: UUID) -> bool:
        """Delete entity strictly checking workspace_id."""
        query = select(self._model_cls).where(
            self._model_cls.id == entity_id,  # type: ignore[attr-defined]
            self._model_cls.workspace_id == workspace_id,  # type: ignore[attr-defined]
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True
