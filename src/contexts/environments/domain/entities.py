"""Environment variable domain entities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from src.shared.base_entity import BaseEntity


class EnvScope(StrEnum):
    """Where an environment variable applies."""

    WORKSPACE = "workspace"
    PROJECT = "project"


@dataclass
class EnvVar(BaseEntity):
    """One encrypted environment variable."""

    workspace_id: UUID | None = None
    scope: EnvScope = EnvScope.PROJECT
    project_id: UUID | None = None
    key: str = ""
    value_ciphertext: str = ""
    is_secret: bool = True
    updated_by: str = ""
