"""SQLAlchemy ORM model for environment variables."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text, Uuid
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.contexts.environments.domain.entities import EnvScope
from src.infrastructure.database.base import Base, CommonColumns


class EnvVarModel(Base, CommonColumns):
    """PostgreSQL env_vars table with RLS enforcement."""

    __tablename__ = "env_vars"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'PROJECT') = (project_id IS NOT NULL)", name="env_scope_project_check"
        ),
        Index("env_vars_lookup", "workspace_id", "scope", "project_id", "key"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope: Mapped[EnvScope] = mapped_column(
        SQLEnum(EnvScope, name="env_scope"), nullable=False, default=EnvScope.PROJECT
    )
    project_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
