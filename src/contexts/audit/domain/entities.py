"""Audit logging domain entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from src.shared.base_entity import BaseEntity


@dataclass
class AuditLogEntry(BaseEntity):
    """Immutable audit trail record."""

    workspace_id: UUID | None = None
    actor_id: str = ""
    action: str = ""
    resource_type: str = ""
    resource_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
