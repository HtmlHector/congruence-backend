"""Worktree application service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.audit.application.service import AuditService
from src.contexts.devbox.application.service import DevboxError, DevboxService
from src.contexts.devbox.domain.entities import Devbox, ProjectLike
from src.contexts.devbox.infrastructure.runner import (
    BoxCommandError,
    BoxNotAwakeError,
)
from src.contexts.terminals.application.service import TerminalService
from src.contexts.usage.application.service import UsageLimitError, UsageService
from src.contexts.worktrees.domain.entities import (
    Worktree,
    WorktreeStatus,
    branch_for_slug,
    is_valid_branch,
    port_base_for_slot,
    project_root_path,
    slugify,
    worktree_path,
)
from src.contexts.worktrees.infrastructure.repository import WorktreeRepository
from src.realtime.events import emit_to_workspace

logger = structlog.get_logger(__name__)

RESTORE_WINDOW = timedelta(days=30)


class WorktreeError(Exception):
    """Domain-level worktree failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorktreeService:
    """Creates and maintains git worktrees inside project devboxes."""

    def __init__(self, session: AsyncSession, devbox_service: DevboxService | None = None) -> None:
        self._session = session
        self._repo = WorktreeRepository(session)
        self._audit = AuditService(session)
        self._usage = UsageService(session)
        self._devbox = devbox_service or DevboxService(session)
        self._terminals = TerminalService(session)

    async def list_for_project(
        self, project_id: UUID, workspace_id: UUID, *, include_deleted: bool = False
    ) -> list[Worktree]:
        """List worktrees for a project, optionally including soft-deleted ones."""
        return list(
            await self._repo.list_for_project(
                project_id, workspace_id, include_deleted=include_deleted
            )
        )

    async def get(
        self, worktree_id: UUID, workspace_id: UUID, project_id: UUID | None = None
    ) -> Worktree | None:
        """Fetch a worktree scoped to its workspace (and optionally project)."""
        if project_id is not None:
            return await self._repo.get_for_project(worktree_id, project_id, workspace_id)
        return await self._repo.get_by_id_and_workspace(worktree_id, workspace_id)

    async def ensure_primary(self, project: ProjectLike, actor_id: str) -> Worktree:
        """Return the project's primary worktree, provisioning its checkout if needed.

        Projects created before primary worktrees existed (or whose provisioning was
        interrupted) have a repository but no recorded checkout; this backfills it.
        """
        workspace_id = project.workspace_id
        project_id = project.id
        if workspace_id is None or project_id is None:
            raise WorktreeError("PROJECT_NOT_READY", "Project has no workspace context")

        existing = await self._repo.get_primary(project_id, workspace_id)
        if existing is not None:
            return existing

        box = await self._devbox_for(project)
        return await self.create_primary(project, actor_id, box)

    async def create_primary(
        self, project: ProjectLike, actor_id: str, box: Devbox
    ) -> Worktree:
        """Record the project's root checkout as its primary worktree.

        The devbox clone already *is* that checkout, so this writes the row without
        running ``git worktree add``. Idempotent per project: the first call wins and
        later calls return the existing primary.
        """
        workspace_id = project.workspace_id
        project_id = project.id
        if workspace_id is None or project_id is None:
            raise WorktreeError("PROJECT_NOT_READY", "Project has no workspace context")
        if not project.repository_id:
            raise WorktreeError("PROJECT_NOT_READY", "Project has no repository bound")

        existing = await self._repo.get_primary(project_id, workspace_id)
        if existing is not None:
            return existing

        default_branch = project.default_branch or "main"
        slot = await self._repo.next_free_slot(project_id, workspace_id)
        worktree = Worktree(
            workspace_id=workspace_id,
            project_id=project_id,
            devbox_id=box.id,
            title=project.name.strip() or default_branch,
            branch=default_branch,
            base_branch=default_branch,
            path=project_root_path(slugify(project.name)),
            slot=slot,
            port_base=port_base_for_slot(
                slot,
                start=settings.devbox.worktree_port_start,
                block=settings.devbox.worktree_port_block,
            ),
            status=WorktreeStatus.READY,
            is_primary=True,
            created_by=actor_id,
        )
        saved = await self._repo.add(worktree)

        await self._audit.record(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="worktree.create",
            resource_type="worktree",
            resource_id=str(saved.id),
            payload={"branch": saved.branch, "primary": True},
        )
        await self._emit(saved, "worktree:ready")
        return saved

    async def create(
        self,
        project: ProjectLike,
        actor_id: str,
        *,
        title: str,
        base_branch: str | None = None,
        branch: str | None = None,
        initial_prompt: str | None = None,
    ) -> Worktree:
        """Create a worktree from a base branch and return it once ready."""
        workspace_id = project.workspace_id
        project_id = project.id
        project_name = project.name
        if workspace_id is None or project_id is None:
            raise WorktreeError("PROJECT_NOT_READY", "Project has no workspace context")

        active = await self._repo.count_active(project_id, workspace_id)
        try:
            await self._usage.ensure_usage_available(workspace_id)
            await self._usage.ensure_worktree_allowed(active)
        except UsageLimitError as exc:
            raise WorktreeError(exc.code, str(exc)) from exc

        base = base_branch or project.default_branch or "main"
        slug = await self._unique_slug(project_id, workspace_id, slugify(title))
        # The user names the branch when they supply one; the title slug is only a
        # fallback so a branch-less request still lands somewhere predictable.
        branch_name = (branch or "").strip() or branch_for_slug(slug)
        await self._ensure_branch_available(project, project_id, workspace_id, branch_name)

        box = await self._devbox_for(project)
        # Heals projects provisioned before primary worktrees existed.
        await self.create_primary(project, actor_id, box)

        slot = await self._repo.next_free_slot(project_id, workspace_id)
        project_dir = slugify(project_name)
        path = worktree_path(project_dir, slug)

        worktree = Worktree(
            workspace_id=workspace_id,
            project_id=project_id,
            devbox_id=box.id,
            title=title.strip() or slug,
            initial_prompt=initial_prompt,
            branch=branch_name,
            base_branch=base,
            path=path,
            slot=slot,
            port_base=port_base_for_slot(
                slot,
                start=settings.devbox.worktree_port_start,
                block=settings.devbox.worktree_port_block,
            ),
            status=WorktreeStatus.CREATING,
            created_by=actor_id,
        )
        saved = await self._repo.add(worktree)

        repo_dir = f"/workspaces/{project_dir}"
        try:
            await self._devbox.exec(box, ["git", "-C", repo_dir, "fetch", "origin", base])
            await self._devbox.exec(
                box,
                [
                    "git",
                    "-C",
                    repo_dir,
                    "worktree",
                    "add",
                    "-b",
                    branch_name,
                    path,
                    f"origin/{base}",
                ],
            )
        except (BoxCommandError, BoxNotAwakeError, DevboxError) as exc:
            saved.status = WorktreeStatus.FAILED
            await self._repo.update(saved)
            logger.warning(
                "worktree_create_failed",
                worktree_id=str(saved.id),
                branch=branch_name,
                error=str(exc),
            )
            raise WorktreeError(
                "WORKTREE_CREATE_FAILED", "Could not create the git worktree"
            ) from exc

        saved.status = WorktreeStatus.READY
        updated = await self._repo.update(saved)

        await self._audit.record(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="worktree.create",
            resource_type="worktree",
            resource_id=str(updated.id),
            payload={"branch": updated.branch, "base_branch": updated.base_branch},
        )
        await self._emit(updated, "worktree:ready")
        return updated

    async def update(
        self,
        worktree: Worktree,
        actor_id: str,
        *,
        title: str | None = None,
        status: WorktreeStatus | None = None,
        pr_number: int | None = None,
        pr_url: str | None = None,
    ) -> Worktree:
        """Update task metadata for a worktree."""
        if title is not None:
            worktree.title = title.strip() or worktree.title
        if status is not None:
            worktree.status = status
        if pr_number is not None:
            worktree.pr_number = pr_number
        if pr_url is not None:
            worktree.pr_url = pr_url
        updated = await self._repo.update(worktree)
        if updated.workspace_id:
            await self._audit.record(
                workspace_id=updated.workspace_id,
                actor_id=actor_id,
                action="worktree.update",
                resource_type="worktree",
                resource_id=str(updated.id),
                payload={"status": updated.status.value, "pr_url": updated.pr_url},
            )
        await self._emit(updated, "worktree:status")
        return updated

    async def delete(self, worktree: Worktree, actor_id: str) -> Worktree:
        """Soft-delete a worktree, close its terminals, and remove its directory."""
        if worktree.deleted_at is not None:
            return worktree
        if worktree.is_primary:
            raise WorktreeError(
                "PRIMARY_WORKTREE",
                "The primary worktree is the project's root checkout and cannot be deleted",
            )
        if worktree.workspace_id is None:
            raise WorktreeError("WORKTREE_NOT_READY", "Worktree has no workspace context")

        await self._terminals.close_for_worktree(worktree)

        worktree.status = WorktreeStatus.DELETED
        worktree.deleted_at = datetime.now(UTC)
        updated = await self._repo.update(worktree)

        await self._audit.record(
            workspace_id=worktree.workspace_id,
            actor_id=actor_id,
            action="worktree.delete",
            resource_type="worktree",
            resource_id=str(worktree.id),
            payload={"branch": worktree.branch},
        )
        await self._emit(updated, "worktree:deleted")
        return updated

    async def restore(self, worktree: Worktree, actor_id: str) -> Worktree:
        """Restore a soft-deleted worktree inside the retention window."""
        if worktree.deleted_at is None:
            return worktree
        if worktree.workspace_id is None:
            raise WorktreeError("WORKTREE_NOT_READY", "Worktree has no workspace context")
        deleted_at = (
            worktree.deleted_at
            if worktree.deleted_at.tzinfo
            else worktree.deleted_at.replace(tzinfo=UTC)
        )
        if datetime.now(UTC) - deleted_at > RESTORE_WINDOW:
            raise WorktreeError(
                "RESTORE_WINDOW_EXPIRED", "Worktrees can only be restored within 30 days"
            )

        box = (
            await self._devbox.get_live(worktree.project_id, worktree.workspace_id)
            if worktree.project_id
            else None
        )
        if box is None or box.id != worktree.devbox_id:
            raise WorktreeError(
                "BOX_REBUILT", "The project devbox was rebuilt; restore the branch instead"
            )

        worktree.deleted_at = None
        worktree.status = WorktreeStatus.READY
        updated = await self._repo.update(worktree)
        await self._audit.record(
            workspace_id=worktree.workspace_id,
            actor_id=actor_id,
            action="worktree.restore",
            resource_type="worktree",
            resource_id=str(worktree.id),
        )
        await self._emit(updated, "worktree:ready")
        return updated

    async def _devbox_for(self, project: ProjectLike) -> Devbox:
        """Return the project's live box, provisioning it when absent."""
        try:
            return await self._devbox.get_or_provision(project)
        except DevboxError as exc:
            raise WorktreeError(exc.code, str(exc)) from exc

    async def _ensure_branch_available(
        self,
        project: ProjectLike,
        project_id: UUID,
        workspace_id: UUID,
        branch_name: str,
    ) -> None:
        """Raise unless ``branch_name`` can be checked out in a new worktree."""
        if not is_valid_branch(branch_name):
            raise WorktreeError(
                "INVALID_BRANCH",
                "Branch names may only use letters, numbers, '.', '_', '/' and '-'",
            )
        if branch_name == (project.default_branch or "main"):
            raise WorktreeError(
                "BRANCH_EXISTS",
                f"{branch_name} is checked out in the primary worktree",
            )
        if await self._repo.get_by_branch(project_id, workspace_id, branch_name) is not None:
            raise WorktreeError(
                "BRANCH_EXISTS", f"{branch_name} is already checked out in this project"
            )

    async def _unique_slug(self, project_id: UUID, workspace_id: UUID, base: str) -> str:
        existing = {
            worktree.branch.split("/")[-1]
            for worktree in await self._repo.list_for_project(project_id, workspace_id)
        }
        slug = base
        counter = 2
        while slug in existing:
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    async def _emit(self, worktree: Worktree, event_name: str) -> None:
        if not worktree.workspace_id:
            return
        await emit_to_workspace(
            workspace_id=str(worktree.workspace_id),
            event_name=event_name,
            payload={
                "worktree_id": str(worktree.id),
                "project_id": str(worktree.project_id),
                "branch": worktree.branch,
                "status": worktree.status.value,
                "pr_url": worktree.pr_url,
            },
        )
