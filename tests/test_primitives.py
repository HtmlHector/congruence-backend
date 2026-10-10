"""Tests for shared backend template primitives."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from src.contexts.audit.infrastructure.models import AuditLogModel
from src.contexts.projects.domain.entities import Project, ProjectStatus
from src.contexts.projects.infrastructure.models import ProjectModel
from src.shared import TenantScopedRepository, audited, broadcast_workspace_event


class SampleProjectRepository(TenantScopedRepository[ProjectModel, Project]):
    """Concrete repository built using the TenantScopedRepository primitive."""

    @property
    def _model_cls(self) -> type[ProjectModel]:
        return ProjectModel

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

    def _to_model(self, entity: Project) -> ProjectModel:
        return ProjectModel(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            workspace_id=entity.workspace_id,
            name=entity.name,
            description=entity.description,
            status=entity.status,
            metadata_=entity.metadata,
        )


class SampleService:
    """Sample application service utilizing @audited decorator."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.repo = SampleProjectRepository(session)

    @audited(action="sample.create", resource_type="project")
    async def create_item(
        self,
        workspace_id: UUID,
        actor_id: str,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> Project:
        _ = actor_id
        project = Project(
            id=uuid7(),
            workspace_id=workspace_id,
            name=name,
            status=ProjectStatus.ACTIVE,
            metadata=metadata or {},
        )
        return await self.repo.add(project)


@pytest.mark.asyncio
async def test_tenant_scoped_repository_crud(db_session: AsyncSession) -> None:
    ws_id = uuid7()
    repo = SampleProjectRepository(db_session)

    # 1. Add entity
    p1 = Project(
        id=uuid7(),
        workspace_id=ws_id,
        name="Scoped Primitive Project",
        status=ProjectStatus.ACTIVE,
    )
    saved = await repo.add(p1)
    assert saved.id == p1.id

    # 2. Get by ID and workspace
    fetched = await repo.get_by_id_and_workspace(saved.id, ws_id)
    assert fetched is not None
    assert fetched.name == "Scoped Primitive Project"

    # 3. Isolation check: wrong workspace returns None
    other_ws = uuid7()
    wrong = await repo.get_by_id_and_workspace(saved.id, other_ws)
    assert wrong is None

    # 4. List by workspace
    items = await repo.list_by_workspace(ws_id)
    assert len(items) == 1
    assert items[0].id == saved.id

    # 5. Delete for workspace
    deleted = await repo.delete_for_workspace(saved.id, ws_id)
    assert deleted is True
    assert await repo.get_by_id_and_workspace(saved.id, ws_id) is None


@pytest.mark.asyncio
async def test_audited_decorator_automatically_records_audit_log(
    db_session: AsyncSession,
) -> None:
    ws_id = uuid7()
    actor_id = "user_audited_test_123"

    service = SampleService(db_session)
    project = await service.create_item(
        workspace_id=ws_id,
        actor_id=actor_id,
        name="Audited Resource",
    )

    assert project is not None

    # Verify audit log was persisted automatically
    from sqlalchemy import select

    query = select(AuditLogModel).where(
        AuditLogModel.workspace_id == ws_id,
        AuditLogModel.action == "sample.create",
    )
    result = await db_session.execute(query)
    log_entry = result.scalar_one_or_none()

    assert log_entry is not None
    assert log_entry.actor_id == actor_id
    assert log_entry.resource_type == "project"
    assert log_entry.resource_id == str(project.id)
    assert log_entry.payload.get("name") == "Audited Resource"


@pytest.mark.asyncio
async def test_broadcast_workspace_event_does_not_throw() -> None:
    ws_id = uuid7()
    # Ensure broadcast helper can be called cleanly without error
    await broadcast_workspace_event(
        workspace_id=ws_id,
        event_name="primitive:ping",
        payload={"message": "pong"},
    )
