"""Project application service implementing commands, queries, and event dispatches."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.contexts.audit.application.service import AuditService
from src.contexts.devbox.domain.entities import Devbox
from src.contexts.projects.domain.entities import Project, ProjectStatus, SetupStatus
from src.contexts.projects.domain.events import (
    ProjectCreatedEvent,
    ProjectUpdatedEvent,
)
from src.contexts.projects.infrastructure.repository import ProjectRepository
from src.infrastructure.messaging.bus import message_bus
from src.infrastructure.messaging.topology import (
    RK_PROJECTS_CREATED,
    RK_PROJECTS_DELETED,
    RK_PROJECTS_UPDATED,
)
from src.realtime.events import emit_to_workspace

logger = structlog.get_logger(__name__)


class ProjectService:
    """Orchestrates project operations and dispatches side-effects."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ProjectRepository(session)
        self._audit = AuditService(session)

    async def create_project(
        self,
        workspace_id: UUID,
        actor_id: str,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        repository_id: str | None = None,
        repo_provider: str = "github",
        default_branch: str = "main",
        root_path: str = "",
        setup_command: str | None = None,
    ) -> Project:
        """Create a new project, provision its devbox, and record audit/realtime events."""
        project = Project(
            workspace_id=workspace_id,
            name=name,
            description=description,
            status=ProjectStatus.ACTIVE,
            metadata=metadata or {},
            repository_id=repository_id,
            repo_provider=repo_provider,
            default_branch=default_branch,
            root_path=root_path,
            setup_command=setup_command,
            setup_status=SetupStatus.PENDING,
            created_by=actor_id,
        )
        project.record_event(
            ProjectCreatedEvent(
                project_id=project.id,
                workspace_id=workspace_id,
                name=name,
            )
        )

        saved = await self._repo.add(project)

        # 1. Audit trail
        await self._audit.record(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="project.create",
            resource_type="project",
            resource_id=str(saved.id),
            payload={"name": name},
        )

        # 2. Realtime Socket.IO notification
        await emit_to_workspace(
            workspace_id=str(workspace_id),
            event_name="project:created",
            payload={"project_id": str(saved.id), "name": saved.name, "status": saved.status.value},
        )

        # 3. RabbitMQ event bus
        await message_bus.publish(
            routing_key=RK_PROJECTS_CREATED,
            payload={
                "project_id": str(saved.id),
                "workspace_id": str(workspace_id),
                "name": saved.name,
            },
            workspace_id=str(workspace_id),
        )

        if saved.repository_id:
            box = await self._provision_devbox(saved, actor_id)
            if box is not None:
                await self._create_primary_worktree(saved, box, actor_id)

        return saved

    async def _provision_devbox(self, project: Project, actor_id: str) -> Devbox | None:
        """Best-effort devbox provisioning; failures are surfaced via setup_status.

        Returns the live box when the repository checkout is usable. A failing setup
        command still leaves a clone behind, so that case keeps the box and lets the
        project open on the checkout while the user fixes setup.
        """
        from src.contexts.devbox.application.service import DevboxError, DevboxService

        service = DevboxService(self._session)
        try:
            return await service.get_or_provision(project)
        except DevboxError as exc:
            project.setup_status = SetupStatus.FAILED
            await self._repo.update(project)
            if project.workspace_id:
                await self._audit.record(
                    workspace_id=project.workspace_id,
                    actor_id=actor_id,
                    action="project.provision_failed",
                    resource_type="project",
                    resource_id=str(project.id),
                    payload={"code": exc.code},
                )
            if exc.code != "SETUP_FAILED" or project.workspace_id is None:
                return None
            return await service.get_live(project.id, project.workspace_id)

    async def _create_primary_worktree(
        self, project: Project, box: Devbox, actor_id: str
    ) -> None:
        """Give every provisioned project a primary (root checkout) worktree."""
        from src.contexts.worktrees.application.service import WorktreeError, WorktreeService

        try:
            await WorktreeService(self._session).create_primary(project, actor_id, box)
        except WorktreeError as exc:
            logger.warning(
                "primary_worktree_create_failed",
                project_id=str(project.id),
                code=exc.code,
                error=str(exc),
            )

    async def _destroy_devbox(self, project_id: UUID, workspace_id: UUID, actor_id: str) -> None:
        """Tear down the project's devbox so containers are not leaked on delete."""
        from src.contexts.devbox.application.service import DevboxError, DevboxService
        from src.contexts.devbox.infrastructure.providers.base import ProviderError

        devbox_service = DevboxService(self._session)
        box = await devbox_service.get_live(project_id, workspace_id)
        if box is None:
            return
        try:
            await devbox_service.destroy(box, actor_id=actor_id)
        except ProviderError as exc:
            logger.warning("devbox_destroy_failed", project_id=str(project_id), error=str(exc))
        except DevboxError as exc:
            logger.warning("devbox_destroy_failed", project_id=str(project_id), error=exc.code)

    async def get_project(self, project_id: UUID, workspace_id: UUID) -> Project | None:
        """Fetch project ensuring tenant isolation."""
        return await self._repo.get_by_id_and_workspace(project_id, workspace_id)

    async def list_projects(
        self,
        workspace_id: UUID,
        status: ProjectStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Project]:
        """List projects for the active workspace."""
        projects = await self._repo.list_by_workspace(workspace_id, status, limit, offset)
        return list(projects)

    async def update_project(
        self,
        project_id: UUID,
        workspace_id: UUID,
        actor_id: str,
        name: str | None = None,
        description: str | None = None,
        status: ProjectStatus | None = None,
        metadata: dict[str, Any] | None = None,
        default_branch: str | None = None,
        setup_command: str | None = None,
    ) -> Project | None:
        """Update an existing project."""
        project = await self._repo.get_by_id_and_workspace(project_id, workspace_id)
        if not project:
            return None

        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        if status is not None:
            project.status = status
        if metadata is not None:
            project.metadata = metadata
        if default_branch is not None:
            project.default_branch = default_branch
        if setup_command is not None:
            project.setup_command = setup_command

        project.record_event(ProjectUpdatedEvent(project_id=project.id, workspace_id=workspace_id))
        updated = await self._repo.update(project)

        # Audit trail
        await self._audit.record(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="project.update",
            resource_type="project",
            resource_id=str(updated.id),
            payload={"status": updated.status.value},
        )

        # Realtime Socket.IO
        await emit_to_workspace(
            workspace_id=str(workspace_id),
            event_name="project:updated",
            payload={"project_id": str(updated.id), "status": updated.status.value},
        )

        # RabbitMQ
        await message_bus.publish(
            routing_key=RK_PROJECTS_UPDATED,
            payload={"project_id": str(updated.id), "workspace_id": str(workspace_id)},
            workspace_id=str(workspace_id),
        )

        return updated

    async def delete_project(
        self,
        project_id: UUID,
        workspace_id: UUID,
        actor_id: str,
    ) -> bool:
        """Delete project."""
        project = await self._repo.get_by_id_and_workspace(project_id, workspace_id)
        if not project:
            return False

        await self._destroy_devbox(project_id, workspace_id, actor_id)

        success = await self._repo.delete(project_id)
        if success:
            await self._audit.record(
                workspace_id=workspace_id,
                actor_id=actor_id,
                action="project.delete",
                resource_type="project",
                resource_id=str(project_id),
            )
            await emit_to_workspace(
                workspace_id=str(workspace_id),
                event_name="project:deleted",
                payload={"project_id": str(project_id)},
            )
            await message_bus.publish(
                routing_key=RK_PROJECTS_DELETED,
                payload={"project_id": str(project_id), "workspace_id": str(workspace_id)},
                workspace_id=str(workspace_id),
            )
        return success
