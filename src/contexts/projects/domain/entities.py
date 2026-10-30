"""Project sample domain entities and lifecycle statuses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from src.shared.base_aggregate import BaseAggregateRoot


class ProjectStatus(StrEnum):
    """Project lifecycle status."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SetupStatus(StrEnum):
    """Progress of the project's one-time environment setup."""

    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


@dataclass
class Project(BaseAggregateRoot):
    """A project bound to one repository and one persistent devbox."""

    workspace_id: UUID | None = None
    name: str = ""
    description: str | None = None
    status: ProjectStatus = ProjectStatus.DRAFT
    metadata: dict[str, Any] = field(default_factory=dict)
    repository_id: str | None = None
    repo_provider: str = "github"
    default_branch: str = "main"
    root_path: str = ""
    setup_command: str | None = None
    setup_status: SetupStatus = SetupStatus.PENDING
    setup_log_ref: str | None = None
    base_image_ref: str | None = None
    created_by: str | None = None
