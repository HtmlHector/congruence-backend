"""SQLAlchemy ORM model for projects table."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import JSON, ForeignKey, String, Text, Uuid
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.contexts.projects.domain.entities import ProjectStatus, SetupStatus
from src.infrastructure.database.base import Base, CommonColumns


class ProjectModel(Base, CommonColumns):
    """PostgreSQL projects table with Row-Level Security enforcement."""

    __tablename__ = "projects"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        SQLEnum(ProjectStatus, name="project_status"),
        default=ProjectStatus.DRAFT,
        nullable=False,
        index=True,
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    repository_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    repo_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="github")
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    root_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    setup_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    setup_status: Mapped[SetupStatus] = mapped_column(
        SQLEnum(SetupStatus, name="setup_status"),
        nullable=False,
        default=SetupStatus.PENDING,
        index=True,
    )
    setup_log_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    base_image_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
