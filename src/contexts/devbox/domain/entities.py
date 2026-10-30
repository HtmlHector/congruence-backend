"""Devbox domain entities and lifecycle statuses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from src.shared.base_aggregate import BaseAggregateRoot


class DevboxStatus(StrEnum):
    """Lifecycle of a persistent project devbox."""

    PROVISIONING = "provisioning"
    AWAKE = "awake"
    PAUSING = "pausing"
    PAUSED = "paused"
    WAKING = "waking"
    ERROR = "error"
    DESTROYED = "destroyed"


class ProjectLike(Protocol):
    """The slice of a project the devbox lifecycle needs."""

    id: UUID
    workspace_id: UUID | None
    name: str
    default_branch: str
    base_image_ref: str | None
    repository_id: str | None
    repo_provider: str
    setup_command: str | None


LIVE_DEVBOX_STATUSES = frozenset(
    {
        DevboxStatus.PROVISIONING,
        DevboxStatus.AWAKE,
        DevboxStatus.PAUSING,
        DevboxStatus.PAUSED,
        DevboxStatus.WAKING,
    }
)


@dataclass
class Devbox(BaseAggregateRoot):
    """A persistent Linux machine associated with exactly one project."""

    workspace_id: UUID | None = None
    project_id: UUID | None = None
    provider: str = "local"
    provider_box_id: str | None = None
    region: str = "local"
    status: DevboxStatus = DevboxStatus.PROVISIONING
    image_ref: str = ""
    cpu: int = 2
    memory_mb: int = 4096
    disk_gb: int = 40
    last_activity_at: datetime | None = None
    last_resumed_at: datetime | None = None
    paused_at: datetime | None = None
    awake_seconds_total: int = 0
    error_code: str | None = None

    @property
    def is_live(self) -> bool:
        """Return True while the box occupies provider capacity."""
        return self.status in LIVE_DEVBOX_STATUSES

    @property
    def is_awake(self) -> bool:
        """Return True when commands can be executed against the box."""
        return self.status in {DevboxStatus.AWAKE, DevboxStatus.PROVISIONING}
