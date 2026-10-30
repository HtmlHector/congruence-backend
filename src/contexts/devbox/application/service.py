"""Devbox application service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.audit.application.service import AuditService
from src.contexts.devbox.domain.entities import Devbox, DevboxStatus, ProjectLike
from src.contexts.devbox.infrastructure.providers import BoxSpec, DevboxProvider, get_provider
from src.contexts.devbox.infrastructure.providers.base import ProviderError
from src.contexts.devbox.infrastructure.repository import DevboxRepository
from src.contexts.devbox.infrastructure.runner import (
    BoxCommandRunner,
    BoxNotAwakeError,
    ExecResult,
    get_runner,
)
from src.contexts.worktrees.domain.entities import slugify
from src.realtime.events import emit_to_workspace

CLONE_TIMEOUT_SECONDS = 900
SETUP_TIMEOUT_SECONDS = 900
PROBE_TIMEOUT_SECONDS = 30

# Git hosts and the username each one expects in an HTTPS credential URL.
REPOSITORY_HOSTS = {
    "github": "github.com",
    "gitlab": "gitlab.com",
    "bitbucket": "bitbucket.org",
}
REPOSITORY_TOKEN_USERNAMES = {
    "github": "x-access-token",
    "gitlab": "oauth2",
    "bitbucket": "x-token-auth",
}


def clone_url(repository_id: str, repo_provider: str) -> str:
    """Build an HTTPS clone URL from an ``owner/repo`` slug or pass a URL through."""
    if "://" in repository_id or repository_id.startswith("git@"):
        return repository_id
    provider = (repo_provider or "github").lower()
    host = REPOSITORY_HOSTS.get(provider, "github.com")
    return f"https://{host}/{repository_id.removesuffix('.git')}.git"


logger = structlog.get_logger(__name__)


class DevboxError(Exception):
    """Domain-level devbox failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize datetimes returned by backends that drop tzinfo (SQLite)."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class DevboxService:
    """Owns the lifecycle of project devboxes."""

    def __init__(
        self,
        session: AsyncSession,
        provider: DevboxProvider | None = None,
        runner: BoxCommandRunner | None = None,
    ) -> None:
        self._session = session
        self._repo = DevboxRepository(session)
        self._audit = AuditService(session)
        self._provider = provider or get_provider()
        self._runner = runner or get_runner(self._provider.name)

    async def get_live(self, project_id: UUID, workspace_id: UUID) -> Devbox | None:
        """Return the live box for a project."""
        return await self._repo.get_live_for_project(project_id, workspace_id)

    async def get_or_provision(self, project: ProjectLike) -> Devbox:
        """Return the project's live box, provisioning one when absent.

        ``project`` is a project domain entity (duck-typed to avoid a cycle).
        """
        project_id = project.id
        workspace_id = project.workspace_id
        if workspace_id is None:
            raise DevboxError("PROJECT_NOT_READY", "Project has no workspace")

        existing = await self._repo.get_live_for_project(project_id, workspace_id)
        if existing:
            await self._ensure_repository(existing, project)
            return existing

        from src.contexts.usage.application.service import UsageLimitError, UsageService

        usage = UsageService(self._session)
        live_boxes = await self._repo.list_live_for_workspace(workspace_id)
        try:
            await usage.ensure_box_allowed(workspace_id, len(live_boxes))
            await usage.ensure_usage_available(workspace_id)
        except UsageLimitError as exc:
            raise DevboxError(exc.code, str(exc)) from exc

        image_ref = project.base_image_ref or settings.devbox.image_ref
        box = Devbox(
            workspace_id=workspace_id,
            project_id=project_id,
            provider=self._provider.name,
            region=settings.devbox.region,
            image_ref=image_ref,
            cpu=settings.devbox.default_cpu,
            memory_mb=settings.devbox.default_memory_mb,
            disk_gb=settings.devbox.default_disk_gb,
        )
        saved = await self._repo.add(box)

        spec = BoxSpec(
            name=f"cng-{str(project_id)[:8]}-{str(saved.id)[:8]}",
            image_ref=saved.image_ref,
            region=saved.region,
            cpu=saved.cpu,
            memory_mb=saved.memory_mb,
            disk_gb=saved.disk_gb,
            env={"CONGRUENCE_PROJECT_ID": str(project_id)},
            metadata={"workspace_id": str(workspace_id), "project_id": str(project_id)},
        )
        try:
            provider_box = await self._provider.create(spec)
        except ProviderError as exc:
            saved.status = DevboxStatus.ERROR
            saved.error_code = "PROVIDER_CREATE_FAILED"
            await self._repo.update(saved)
            await self._audit.record(
                workspace_id=workspace_id,
                actor_id="system",
                action="devbox.create_failed",
                resource_type="devbox",
                resource_id=str(saved.id),
                payload={"error": str(exc)},
            )
            logger.error("devbox_create_failed", project_id=str(project_id), error=str(exc))
            raise DevboxError("BOX_UNAVAILABLE", "Could not provision a devbox") from exc

        now = _utcnow()
        saved.provider_box_id = provider_box.box_id
        saved.status = DevboxStatus.AWAKE
        saved.last_resumed_at = now
        saved.last_activity_at = now
        updated = await self._repo.update(saved)

        await self._audit.record(
            workspace_id=workspace_id,
            actor_id="system",
            action="devbox.create",
            resource_type="devbox",
            resource_id=str(updated.id),
            payload={"provider": updated.provider, "region": updated.region},
        )
        await self._emit(updated, "devbox:status")

        await self._ensure_repository(updated, project)
        return updated

    async def _ensure_repository(self, box: Devbox, project: ProjectLike) -> None:
        """Clone the project repository and run its setup command once per box.

        Called for fresh boxes and for boxes that were booted without a repository,
        so retries heal a partial provision without recloning on every worktree.
        """
        repository_id = project.repository_id
        if not repository_id:
            return

        if box.status == DevboxStatus.PAUSED:
            box = await self.resume(box, actor_id="system")

        project_dir = f"/workspaces/{slugify(project.name)}"
        cloned = await self._runner.exec(
            box, ["test", "-d", f"{project_dir}/.git"], timeout=PROBE_TIMEOUT_SECONDS
        )
        if not cloned.ok:
            # Private repositories need a credential installed before the clone.
            # A GitHub App installation token also keeps working for later pushes
            # through the terminal, because the helper persists it in the box.
            token = await self._repository_token(project)
            if token:
                await self._install_git_credentials(box, token, project.repo_provider)
            result = await self._runner.exec(
                box,
                [
                    "git",
                    "clone",
                    "--branch",
                    project.default_branch or "main",
                    clone_url(repository_id, project.repo_provider),
                    project_dir,
                ],
                timeout=CLONE_TIMEOUT_SECONDS,
            )
            if not result.ok:
                logger.error(
                    "devbox_clone_failed",
                    repository=repository_id,
                    exit_code=result.exit_code,
                    stderr=result.stderr[:500],
                )
                raise DevboxError(
                    "REPO_CLONE_FAILED", f"Could not clone repository {repository_id}"
                )

        setup_command = project.setup_command
        if not setup_command:
            return

        marker = f"{project_dir}/.congruence-setup-complete"
        done = await self._runner.exec(box, ["test", "-f", marker], timeout=PROBE_TIMEOUT_SECONDS)
        if done.ok:
            return

        setup = await self._runner.exec(
            box,
            ["sh", "-lc", setup_command],
            cwd=project_dir,
            timeout=SETUP_TIMEOUT_SECONDS,
        )
        if not setup.ok:
            logger.error(
                "devbox_setup_failed",
                project_id=str(project.id),
                exit_code=setup.exit_code,
                stderr=setup.stderr[:500],
            )
            raise DevboxError("SETUP_FAILED", "Project setup command failed")

        await self._runner.exec(box, ["touch", marker], timeout=PROBE_TIMEOUT_SECONDS)

    async def _repository_token(self, project: ProjectLike) -> str | None:
        """Return a clone token for the project's repository, if one is available."""
        repository_id = project.repository_id
        workspace_id = project.workspace_id
        if not repository_id or workspace_id is None:
            return None

        from src.contexts.integrations.application.service import IntegrationsService

        try:
            return await IntegrationsService(self._session).get_repository_token(
                workspace_id, repository_id, project.repo_provider
            )
        except Exception as exc:  # pragma: no cover - never block provisioning
            logger.warning("devbox_clone_token_failed", error=str(exc))
            return None

    async def _install_git_credentials(
        self, box: Devbox, token: str, repo_provider: str = "github"
    ) -> None:
        """Persist a git host credential in the box so clones and pushes authenticate."""
        provider = (repo_provider or "github").lower()
        username = REPOSITORY_TOKEN_USERNAMES.get(provider, "x-access-token")
        host = REPOSITORY_HOSTS.get(provider, "github.com")
        script = (
            "git config --global credential.helper store && "
            f'printf "https://{username}:%s@{host}\\n" "$CNG_GIT_TOKEN" '
            "> /root/.git-credentials && chmod 600 /root/.git-credentials"
        )
        await self._runner.exec(
            box,
            ["sh", "-lc", script],
            env={"CNG_GIT_TOKEN": token},
            timeout=PROBE_TIMEOUT_SECONDS,
        )

    async def pause(self, box: Devbox, actor_id: str) -> Devbox:
        """Pause a box, accumulating its awake time."""
        if box.status == DevboxStatus.PAUSED:
            return box
        if not box.provider_box_id:
            raise DevboxError("BOX_UNAVAILABLE", "Devbox has no provider identity")

        box.status = DevboxStatus.PAUSING
        await self._repo.update(box)
        await self._provider.pause(box.provider_box_id)

        now = _utcnow()
        if box.last_resumed_at:
            elapsed = (now - _as_utc(box.last_resumed_at)).total_seconds()
            box.awake_seconds_total += max(0, int(elapsed))
        box.status = DevboxStatus.PAUSED
        box.paused_at = now
        box.last_resumed_at = None
        updated = await self._repo.update(box)

        await self._audit.record(
            workspace_id=box.workspace_id or UUID(int=0),
            actor_id=actor_id,
            action="devbox.pause",
            resource_type="devbox",
            resource_id=str(box.id),
            payload={"awake_seconds_total": updated.awake_seconds_total},
        )
        await self._emit(updated, "devbox:paused")
        return updated

    async def resume(self, box: Devbox, actor_id: str) -> Devbox:
        """Resume a paused box."""
        if box.status == DevboxStatus.AWAKE:
            return box
        if not box.provider_box_id:
            raise DevboxError("BOX_UNAVAILABLE", "Devbox has no provider identity")

        box.status = DevboxStatus.WAKING
        await self._repo.update(box)
        await self._provider.resume(box.provider_box_id)

        now = _utcnow()
        box.status = DevboxStatus.AWAKE
        box.last_resumed_at = now
        box.last_activity_at = now
        box.paused_at = None
        updated = await self._repo.update(box)

        await self._audit.record(
            workspace_id=box.workspace_id or UUID(int=0),
            actor_id=actor_id,
            action="devbox.resume",
            resource_type="devbox",
            resource_id=str(box.id),
            payload={},
        )
        await self._emit(updated, "devbox:resumed")
        return updated

    async def touch(self, box: Devbox) -> Devbox:
        """Record activity so the idle reconciler keeps the box awake."""
        box.last_activity_at = _utcnow()
        return await self._repo.update(box)

    async def destroy(self, box: Devbox, actor_id: str) -> Devbox:
        """Permanently delete the provider box."""
        if box.provider_box_id:
            await self._provider.destroy(box.provider_box_id)
        box.status = DevboxStatus.DESTROYED
        box.provider_box_id = None
        updated = await self._repo.update(box)
        await self._audit.record(
            workspace_id=box.workspace_id or UUID(int=0),
            actor_id=actor_id,
            action="devbox.destroy",
            resource_type="devbox",
            resource_id=str(box.id),
        )
        await self._emit(updated, "devbox:destroyed")
        return updated

    async def reset(self, project: ProjectLike, actor_id: str) -> Devbox:
        """Destroy the current box and provision a clean one."""
        workspace_id = project.workspace_id
        project_id = project.id
        if workspace_id is None or project_id is None:
            raise DevboxError("PROJECT_NOT_READY", "Project has no workspace context")

        existing = await self._repo.get_live_for_project(project_id, workspace_id)
        if existing:
            await self.destroy(existing, actor_id)
        return await self.get_or_provision(project)

    async def exec(
        self,
        box: Devbox,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecResult:
        """Run a command in the box, waking it first if needed."""
        if box.status == DevboxStatus.PAUSED:
            box = await self.resume(box, actor_id="system")
        if box.status is not DevboxStatus.AWAKE:
            raise BoxNotAwakeError(f"Devbox {box.id} is {box.status}")
        return await self._runner.exec(box, command, cwd=cwd, env=env, timeout=timeout)

    async def _emit(self, box: Devbox, event_name: str) -> None:
        if not box.workspace_id:
            return
        await emit_to_workspace(
            workspace_id=str(box.workspace_id),
            event_name=event_name,
            payload={
                "devbox_id": str(box.id),
                "project_id": str(box.project_id),
                "status": box.status.value,
            },
        )
