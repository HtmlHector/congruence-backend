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


@dataclass
class Project(BaseAggregateRoot):
    """Sample domain entity demonstrating DDD and multi-tenancy."""

    workspace_id: UUID | None = None
    name: str = ""
    description: str | None = None
    status: ProjectStatus = ProjectStatus.DRAFT
    metadata: dict[str, Any] = field(default_factory=dict)
