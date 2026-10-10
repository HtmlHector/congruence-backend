"""Workspace multi-tenant domain entities and roles."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from src.shared.base_aggregate import BaseAggregateRoot
from src.shared.base_entity import BaseEntity


class WorkspaceRole(StrEnum):
    """RBAC roles inside a workspace."""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


@dataclass
class WorkspaceMember(BaseEntity):
    """Membership relationship associating a user with a workspace."""

    workspace_id: UUID | None = None
    user_id: UUID | None = None
    role: WorkspaceRole = WorkspaceRole.MEMBER


@dataclass
class Workspace(BaseAggregateRoot):
    """Multi-tenant organization boundary."""

    name: str = ""
    slug: str = ""
    stripe_customer_id: str | None = None
    members: list[WorkspaceMember] = field(default_factory=list)
