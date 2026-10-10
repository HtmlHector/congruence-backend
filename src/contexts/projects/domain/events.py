"""Domain events emitted by Project aggregate."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from src.shared.domain_event import DomainEvent


@dataclass(frozen=True)
class ProjectCreatedEvent(DomainEvent):
    """Fired when a new project is created."""

    event_name: str = "project.created"
    project_id: UUID | None = None
    workspace_id: UUID | None = None
    name: str = ""


@dataclass(frozen=True)
class ProjectUpdatedEvent(DomainEvent):
    """Fired when project details or status change."""

    event_name: str = "project.updated"
    project_id: UUID | None = None
    workspace_id: UUID | None = None


@dataclass(frozen=True)
class ProjectDeletedEvent(DomainEvent):
    """Fired when project is deleted."""

    event_name: str = "project.deleted"
    project_id: UUID | None = None
    workspace_id: UUID | None = None
