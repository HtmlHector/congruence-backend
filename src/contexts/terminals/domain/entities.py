"""Terminal domain entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.shared.base_aggregate import BaseAggregateRoot


class TerminalKind(StrEnum):
    """What a terminal is running."""

    SHELL = "shell"
    AGENT = "agent"
    LOGIN = "login"


class TerminalStatus(StrEnum):
    """Lifecycle of a terminal session."""

    STARTING = "starting"
    OPEN = "open"
    DETACHED = "detached"
    LOST = "lost"
    CLOSED = "closed"


SUPPORTED_AGENTS: dict[str, str] = {
    "claude": "anthropic",
    "codex": "openai",
}


def provider_for_agent(agent: str | None) -> str | None:
    """Map an agent CLI name to its credential provider."""
    return SUPPORTED_AGENTS.get((agent or "").lower())


@dataclass
class Terminal(BaseAggregateRoot):
    """One tmux session inside a worktree."""

    workspace_id: UUID | None = None
    worktree_id: UUID | None = None
    devbox_id: UUID | None = None
    tmux_session: str | None = None
    kind: TerminalKind = TerminalKind.SHELL
    agent: str | None = None
    title: str | None = None
    status: TerminalStatus = TerminalStatus.STARTING
    created_by: str = ""
    credential_id: UUID | None = None
    recording_enabled: bool = True
    cols: int = 120
    rows: int = 32
    last_seq: int = 0
    scrollback_bytes: int = 0
    last_attached_at: datetime | None = None
    closed_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """Return True while the session exists."""
        return self.status in {
            TerminalStatus.STARTING,
            TerminalStatus.OPEN,
            TerminalStatus.DETACHED,
            TerminalStatus.LOST,
        }
