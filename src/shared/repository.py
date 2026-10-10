"""Abstract repository interface for domain persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TypeVar
from uuid import UUID

from src.shared.base_entity import BaseEntity

T = TypeVar("T", bound=BaseEntity)


class AbstractRepository[T: BaseEntity](ABC):
    """Generic async repository interface following DDD conventions."""

    @abstractmethod
    async def get_by_id(self, entity_id: UUID) -> T | None:
        """Fetch entity by primary key."""
        raise NotImplementedError

    @abstractmethod
    async def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[T]:
        """Fetch paginated list of entities."""
        raise NotImplementedError

    @abstractmethod
    async def add(self, entity: T) -> T:
        """Persist a new entity."""
        raise NotImplementedError

    @abstractmethod
    async def update(self, entity: T) -> T:
        """Update an existing entity."""
        raise NotImplementedError

    @abstractmethod
    async def delete(self, entity_id: UUID) -> bool:
        """Remove an entity by ID."""
        raise NotImplementedError
