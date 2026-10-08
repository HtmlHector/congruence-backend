"""SQLAlchemy ORM models for the Congruence platform."""

from __future__ import annotations

from datetime import datetime

import nanoid
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.base import Base, TimestampMixin


def generate_id(prefix: str) -> str:
    """Generate safe URL-friendly nanoid with prefix."""
    return f"{prefix}_{nanoid.generate(size=16)}"


class Organization(Base, TimestampMixin):
    __tablename__ = "congruence_organizations"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("org")
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    plan_tier: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True
    )

    projects: Mapped[list[Project]] = relationship(
        "Project", back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationMember(Base, TimestampMixin):
    """Links an authenticated user to an organization. Projects are visible only to members of their org."""

    __tablename__ = "congruence_organization_members"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("orgm")
    )
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), default="owner", nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_member"),
    )


class Project(Base, TimestampMixin):
    __tablename__ = "congruence_projects"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("proj")
    )
    organization_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(64), default="main", nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), default="private", nullable=False)

    organization: Mapped[Organization] = relationship("Organization", back_populates="projects")
    host: Mapped[Host | None] = relationship(
        "Host", back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    actors: Mapped[list[Actor]] = relationship(
        "Actor", back_populates="project", cascade="all, delete-orphan"
    )
    activity_events: Mapped[list[ActivityEvent]] = relationship(
        "ActivityEvent", back_populates="project", cascade="all, delete-orphan"
    )


class Host(Base, TimestampMixin):
    __tablename__ = "congruence_hosts"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("host")
    )
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_projects.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    backend_type: Mapped[str] = mapped_column(
        String(64), default="process_docker", nullable=False
    )
    provider_instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), default="awake", nullable=False
    )  # 'awake' | 'asleep' | 'waking' | 'sleeping'
    size: Mapped[str] = mapped_column(String(32), default="standard", nullable=False)
    disk_size_gb: Mapped[int] = mapped_column(Integer, default=32, nullable=False)
    awake_seconds_month: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_wake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sleep_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship("Project", back_populates="host")
    lanes: Mapped[list[Lane]] = relationship(
        "Lane", back_populates="host", cascade="all, delete-orphan"
    )
    services: Mapped[list[Service]] = relationship(
        "Service", back_populates="host", cascade="all, delete-orphan"
    )


class Actor(Base, TimestampMixin):
    __tablename__ = "congruence_actors"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("act")
    )
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # 'human' | 'harness_claude' | 'harness_codex' | 'harness_aider'
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), default="watcher", nullable=False
    )  # 'owner' | 'writer' | 'watcher'
    avatar_color: Mapped[str] = mapped_column(String(32), default="#E8804A", nullable=False)
    presence: Mapped[str] = mapped_column(
        String(32), default="offline", nullable=False
    )  # 'online' | 'away' | 'offline'

    project: Mapped[Project] = relationship("Project", back_populates="actors")
    grants: Mapped[list[Grant]] = relationship(
        "Grant", foreign_keys="[Grant.actor_id]", back_populates="actor", cascade="all, delete-orphan"
    )


class Lane(Base, TimestampMixin):
    __tablename__ = "congruence_lanes"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("lane")
    )
    host_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_hosts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    worktree_path: Mapped[str] = mapped_column(String(255), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(128), default="main", nullable=False)
    is_pair_lane: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="ready", nullable=False
    )  # 'ready' | 'running_dev' | 'editing' | 'closed'

    host: Mapped[Host] = relationship("Host", back_populates="lanes")
    grants: Mapped[list[Grant]] = relationship(
        "Grant", back_populates="lane", cascade="all, delete-orphan"
    )
    services: Mapped[list[Service]] = relationship(
        "Service", back_populates="lane", cascade="all, delete-orphan"
    )
    publications: Mapped[list[Publication]] = relationship(
        "Publication", back_populates="lane", cascade="all, delete-orphan"
    )
    harness_auth: Mapped[list[HarnessAuth]] = relationship(
        "HarnessAuth", back_populates="lane", cascade="all, delete-orphan"
    )


class Grant(Base):
    __tablename__ = "congruence_grants"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("grnt")
    )
    lane_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_lanes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_actors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission: Mapped[str] = mapped_column(
        String(32), default="watch", nullable=False
    )  # 'watch' | 'write'
    granted_by_actor_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("congruence_actors.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    lane: Mapped[Lane] = relationship("Lane", back_populates="grants")
    actor: Mapped[Actor] = relationship("Actor", foreign_keys=[actor_id], back_populates="grants")


class Service(Base, TimestampMixin):
    __tablename__ = "congruence_services"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("srv")
    )
    host_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_hosts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lane_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("congruence_lanes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), default="http", nullable=False)
    address_subdomain: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    access_mode: Mapped[str] = mapped_column(
        String(32), default="private", nullable=False
    )  # 'private' | 'public_timeboxed'
    # 'app' is a normal preview/dev port. 'oauth_callback' is a short-lived,
    # single-use surface that only exists to bridge a CLI's loopback OAuth
    # callback; it is never listed in the preview UI and is reaped on use.
    service_kind: Mapped[str] = mapped_column(
        String(32), default="app", nullable=False
    )  # 'app' | 'oauth_callback'
    is_single_use: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    host: Mapped[Host] = relationship("Host", back_populates="services")
    lane: Mapped[Lane | None] = relationship("Lane", back_populates="services")


class HarnessAuth(Base, TimestampMixin):
    """Reports whether a lane's CLI harness is authenticated.

    This table holds no secret, and cannot be made to. Every column is an
    enum, a boolean, a foreign key, or a timestamp; there is no ciphertext
    field because the control plane has no key with which to encrypt or
    decrypt one.

    The credential itself is written by the vendor's own CLI (``claude login``)
    into the host's ``$HOME`` on durable disk. That is why ``state`` is
    *reported by the host* rather than known by us: we probe for the file's
    presence and record the answer, nothing more.

    See ``trd.md`` section 7 and ``prd.md`` Invariant 4 (Host Custody).
    """

    __tablename__ = "congruence_harness_auth"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("hauth")
    )
    lane_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_lanes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    harness: Mapped[str] = mapped_column(String(32), nullable=False)
    # 'claude' | 'codex' | 'opencode' | 'aider'
    state: Mapped[str] = mapped_column(
        String(32), default="disconnected", nullable=False
    )  # 'disconnected' | 'awaiting_user' | 'connected' | 'error'
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lane: Mapped[Lane] = relationship("Lane", back_populates="harness_auth")

    __table_args__ = (
        UniqueConstraint("lane_id", "harness", name="uq_harness_auth_lane_harness"),
    )


class Publication(Base):
    __tablename__ = "congruence_publications"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("pub")
    )
    lane_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_lanes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_actors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="open", nullable=False
    )  # 'open' | 'merged' | 'closed'
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    lane: Mapped[Lane] = relationship("Lane", back_populates="publications")


class GitHubAccount(Base, TimestampMixin):
    __tablename__ = "congruence_github_accounts"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("gh")
    )
    user_id: Mapped[str] = mapped_column(String(64), default="usr_default", nullable=False, index=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_type: Mapped[str] = mapped_column(String(32), default="bearer", nullable=False)
    scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ActivityEvent(Base):
    __tablename__ = "congruence_activity_events"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("evt")
    )
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lane_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship("Project", back_populates="activity_events")


class ChatSession(Base, TimestampMixin):
    __tablename__ = "congruence_chat_sessions"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: generate_id("chat")
    )
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("congruence_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lane_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    harness: Mapped[str] = mapped_column(String(64), default="Antigravity", nullable=False)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="idle", nullable=False)
    messages_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)


