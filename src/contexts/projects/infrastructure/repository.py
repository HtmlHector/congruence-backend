"""Project repository implementation using async SQLAlchemy."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.projects.domain.entities import Project, ProjectStatus
from src.contexts.projects.infrastructure.models import ProjectModel
from src.shared.repository import AbstractRepository


class ProjectRepository(AbstractRepository[Project]):
    """SQLAlchemy implementation of project persistence with tenant filtering."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, model: ProjectModel) -> Project:
        return Project(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            workspace_id=model.workspace_id,
            name=model.name,
            description=model.description,
            status=model.status,
            metadata=dict(model.metadata_),
        )

    async def get_by_id(self, entity_id: UUID) -> Project | None:
        model = await self._session.get(ProjectModel, entity_id)
        return self._to_entity(model) if model else None

    async def get_by_id_and_workspace(self, project_id: UUID, workspace_id: UUID) -> Project | None:
        """Fetch project ensuring it belongs to the specified workspace."""
        query = select(ProjectModel).where(
            ProjectModel.id == project_id,
            ProjectModel.workspace_id == workspace_id,
        )
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_by_workspace(
        self,
        workspace_id: UUID,
        status: ProjectStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Project]:
        """List projects for a workspace with optional status filter."""
        query = select(ProjectModel).where(ProjectModel.workspace_id == workspace_id)
        if status:
            query = query.where(ProjectModel.status == status)
        query = query.order_by(desc(ProjectModel.created_at)).limit(limit).offset(offset)
        result = await self._session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[Project]:
        query = (
            select(ProjectModel).order_by(desc(ProjectModel.created_at)).limit(limit).offset(offset)
        )
        result = await self._session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def add(self, entity: Project) -> Project:
        model = ProjectModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            name=entity.name,
            description=entity.description,
            status=entity.status,
            metadata_=entity.metadata,
        )
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def update(self, entity: Project) -> Project:
        model = await self._session.get(ProjectModel, entity.id)
        if not model:
            raise ValueError(f"Project with id {entity.id} does not exist")
        model.name = entity.name
        model.description = entity.description
        model.status = entity.status
        model.metadata_ = entity.metadata
        model.updated_at = entity.updated_at
        await self._session.flush()
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        model = await self._session.get(ProjectModel, entity_id)
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True
