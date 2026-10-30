"""Terminal application service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.contexts.audit.application.service import AuditService
from src.contexts.terminals.domain.entities import (
    Terminal,
    TerminalKind,
    TerminalStatus,
    provider_for_agent,
)
from src.contexts.terminals.infrastructure.repository import TerminalRepository
from src.contexts.usage.application.service import UsageLimitError, UsageService
from src.contexts.worktrees.domain.entities import Worktree
from src.realtime.events import emit_to_workspace
from src.security.tickets import sign_ticket

logger = structlog.get_logger(__name__)


class TerminalError(Exception):
    """Domain-level terminal failure with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TerminalService:
    """Opens, attaches, and closes terminal sessions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = TerminalRepository(session)
        self._usage = UsageService(session)
        self._audit = AuditService(session)

    async def list_for_worktree(self, worktree_id: UUID, workspace_id: UUID) -> list[Terminal]:
        """List live terminals for a worktree."""
        return list(await self._repo.list_for_worktree(worktree_id, workspace_id))

    async def get(self, terminal_id: UUID, workspace_id: UUID) -> Terminal | None:
        """Fetch a terminal scoped to the active workspace."""
        return await self._repo.get_by_id_and_workspace(terminal_id, workspace_id)

    async def create(
        self,
        worktree: Worktree,
        actor_id: str,
        *,
        kind: TerminalKind = TerminalKind.SHELL,
        agent: str | None = None,
        title: str | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> Terminal:
        """Create a terminal, enforcing plan limits and credential resolution."""
        workspace_id = worktree.workspace_id
        if workspace_id is None or worktree.id is None or worktree.devbox_id is None:
            raise TerminalError("WORKTREE_NOT_READY", "Worktree is missing required context")

        limits = await self._usage.limits_for_workspace(workspace_id)
        active = await self._repo.count_active_for_worktree(worktree.id, workspace_id)
        if active >= limits.terminals_per_worktree:
            raise TerminalError(
                "TERMINAL_LIMIT",
                f"Worktrees allow {limits.terminals_per_worktree} active terminals.",
            )
        if worktree.project_id is None:
            raise TerminalError("WORKTREE_NOT_READY", "Worktree has no project")
        project_active = await self._repo.count_active_for_project(
            worktree.project_id, workspace_id
        )
        if project_active >= settings.devbox.max_terminals_per_project:
            raise TerminalError(
                "TERMINAL_LIMIT",
                f"Projects allow {settings.devbox.max_terminals_per_project} active terminals.",
            )

        credential_id: UUID | None = None
        recording = True
        normalized_agent = (agent or "").lower() or None

        if kind == TerminalKind.LOGIN:
            if not normalized_agent or provider_for_agent(normalized_agent) is None:
                raise TerminalError(
                    "UNSUPPORTED_AGENT", "Login terminals require a supported agent (claude, codex)"
                )
            recording = False
        elif kind == TerminalKind.AGENT:
            if not normalized_agent or provider_for_agent(normalized_agent) is None:
                raise TerminalError(
                    "UNSUPPORTED_AGENT", "Agent terminals require a supported agent (claude, codex)"
                )
            credential = await self._active_credential(
                workspace_id=workspace_id,
                user_id=actor_id,
                provider=provider_for_agent(normalized_agent) or "",
            )
            if credential is None:
                raise TerminalError(
                    "CREDENTIAL_MISSING",
                    "Connect this agent in a login terminal before starting it.",
                )
            credential_id = credential

        terminal = Terminal(
            workspace_id=workspace_id,
            worktree_id=worktree.id,
            devbox_id=worktree.devbox_id,
            kind=kind,
            agent=normalized_agent,
            title=title,
            status=TerminalStatus.OPEN,
            created_by=actor_id,
            credential_id=credential_id,
            recording_enabled=recording,
            cols=cols,
            rows=rows,
        )
        terminal.tmux_session = f"cng-{terminal.id.hex[:12]}"
        saved = await self._repo.add(terminal)

        await self._audit.record(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="terminal.open",
            resource_type="terminal",
            resource_id=str(saved.id),
            payload={"kind": saved.kind.value, "agent": saved.agent},
        )
        await self._emit(saved, "terminal:opened")
        return saved

    async def attach(self, terminal: Terminal, user_id: str) -> str:
        """Mark a terminal attached and return a short-lived stream ticket."""
        if terminal.status is TerminalStatus.CLOSED:
            raise TerminalError("TERMINAL_CLOSED", "This terminal has been closed")
        if not terminal.workspace_id:
            raise TerminalError("TERMINAL_NOT_READY", "Terminal has no workspace context")

        terminal.status = (
            TerminalStatus.OPEN if terminal.status is TerminalStatus.DETACHED else terminal.status
        )
        terminal.last_attached_at = datetime.now(UTC)
        await self._repo.update(terminal)

        return sign_ticket(
            {
                "purpose": "terminal_attach",
                "terminal_id": str(terminal.id),
                "workspace_id": str(terminal.workspace_id),
                "user_id": user_id,
            },
            secret=settings.gateway.ticket_secret,
            ttl_seconds=settings.gateway.ticket_ttl_seconds,
        )

    async def capture_token(self, terminal: Terminal, provider: str) -> str:
        """Return a one-time token the box agent uses to report a captured credential."""
        if terminal.kind is not TerminalKind.LOGIN:
            raise TerminalError("INVALID_TERMINAL", "Capture tokens require a login terminal")
        if not terminal.workspace_id:
            raise TerminalError("TERMINAL_NOT_READY", "Terminal has no workspace context")
        return sign_ticket(
            {
                "purpose": "credential_capture",
                "terminal_id": str(terminal.id),
                "workspace_id": str(terminal.workspace_id),
                "user_id": terminal.created_by,
                "provider": provider,
            },
            secret=settings.devbox.capture_token_secret,
            ttl_seconds=300,
        )

    async def close(self, terminal: Terminal, actor_id: str) -> Terminal:
        """Close a terminal and stop its tmux session."""
        terminal.status = TerminalStatus.CLOSED
        terminal.closed_at = datetime.now(UTC)
        updated = await self._repo.update(terminal)
        if terminal.workspace_id:
            await self._audit.record(
                workspace_id=terminal.workspace_id,
                actor_id=actor_id,
                action="terminal.close",
                resource_type="terminal",
                resource_id=str(terminal.id),
            )
        await self._emit(updated, "terminal:closed")
        return updated

    async def restart(self, terminal: Terminal, actor_id: str) -> Terminal:
        """Recreate a lost tmux session for an existing terminal row."""
        terminal.tmux_session = f"cng-{terminal.id.hex[:12]}"
        terminal.status = TerminalStatus.OPEN
        terminal.closed_at = None
        updated = await self._repo.update(terminal)
        await self._audit.record(
            workspace_id=terminal.workspace_id or UUID(int=0),
            actor_id=actor_id,
            action="terminal.restart",
            resource_type="terminal",
            resource_id=str(terminal.id),
        )
        return updated

    async def close_for_worktree(self, worktree: Worktree) -> None:
        """Close every active terminal before a worktree is torn down."""
        if worktree.id and worktree.workspace_id:
            await self._repo.close_for_worktree(worktree.id, worktree.workspace_id)

    async def _active_credential(
        self, *, workspace_id: UUID, user_id: str, provider: str
    ) -> UUID | None:
        from src.contexts.credentials.application.service import CredentialService

        credential = await CredentialService(self._session).get_active(
            workspace_id=workspace_id, user_id=user_id, provider=provider
        )
        return credential.id if credential else None

    async def _emit(self, terminal: Terminal, event_name: str) -> None:
        if not terminal.workspace_id:
            return
        await emit_to_workspace(
            workspace_id=str(terminal.workspace_id),
            event_name=event_name,
            payload={
                "terminal_id": str(terminal.id),
                "worktree_id": str(terminal.worktree_id),
                "kind": terminal.kind.value,
                "agent": terminal.agent,
                "status": terminal.status.value,
                "created_by": terminal.created_by,
            },
        )


__all__ = ["TerminalError", "TerminalService", "UsageLimitError"]
