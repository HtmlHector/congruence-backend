"""Workspace repository implementation using async SQLAlchemy."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.workspace.domain.entities import Workspace, WorkspaceMember, WorkspaceRole
from src.contexts.workspace.infrastructure.models import WorkspaceMemberModel, WorkspaceModel
from src.shared.repository import AbstractRepository


class WorkspaceRepository(AbstractRepository[Workspace]):
    """SQLAlchemy implementation of workspace persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _to_entity(self, model: WorkspaceModel) -> Workspace:
        members = [
            WorkspaceMember(
                id=m.id,
                created_at=m.created_at,
                updated_at=m.updated_at,
                workspace_id=m.workspace_id,
                user_id=m.user_id,
                role=m.role,
            )
            for m in model.members
        ]
        return Workspace(
            id=model.id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            name=model.name,
            slug=model.slug,
            stripe_customer_id=model.stripe_customer_id,
            members=members,
        )

    async def get_by_id(self, entity_id: UUID) -> Workspace | None:
        model = await self._session.get(WorkspaceModel, entity_id)
        return self._to_entity(model) if model else None

    async def get_by_slug(self, slug: str) -> Workspace | None:
        query = select(WorkspaceModel).where(WorkspaceModel.slug == slug)
        result = await self._session.execute(query)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_for_user(self, user_id: UUID) -> Sequence[Workspace]:
        query = (
            select(WorkspaceModel)
            .join(WorkspaceMemberModel, WorkspaceModel.id == WorkspaceMemberModel.workspace_id)
            .where(WorkspaceMemberModel.user_id == user_id)
        )
        result = await self._session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[Workspace]:
        query = select(WorkspaceModel).limit(limit).offset(offset)
        result = await self._session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def add(self, entity: Workspace) -> Workspace:
        model = WorkspaceModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            name=entity.name,
            slug=entity.slug,
            stripe_customer_id=entity.stripe_customer_id,
        )
        for m in entity.members:
            model.members.append(
                WorkspaceMemberModel(
                    id=m.id,
                    created_at=m.created_at,
                    updated_at=m.updated_at,
                    workspace_id=entity.id,
                    user_id=m.user_id,
                    role=m.role,
                )
            )
        self._session.add(model)
        await self._session.flush()
        return self._to_entity(model)

    async def update(self, entity: Workspace) -> Workspace:
        model = await self._session.get(WorkspaceModel, entity.id)
        if not model:
            raise ValueError(f"Workspace with id {entity.id} does not exist")
        model.name = entity.name
        model.slug = entity.slug
        model.stripe_customer_id = entity.stripe_customer_id
        model.updated_at = entity.updated_at
        await self._session.flush()
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        model = await self._session.get(WorkspaceModel, entity_id)
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True

    async def add_member(
        self, workspace_id: UUID, user_id: UUID, role: WorkspaceRole
    ) -> WorkspaceMember:
        member_model = WorkspaceMemberModel(
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
        )
        self._session.add(member_model)
        await self._session.flush()
        return WorkspaceMember(
            id=member_model.id,
            created_at=member_model.created_at,
            updated_at=member_model.updated_at,
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
        )
