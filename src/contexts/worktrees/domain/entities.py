"""Worktree domain entities and naming helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.shared.base_aggregate import BaseAggregateRoot


class WorktreeStatus(StrEnum):
    """Lifecycle of a task worktree."""

    CREATING = "creating"
    READY = "ready"
    WORKING = "working"
    NEEDS_REVIEW = "needs_review"
    MERGED = "merged"
    FAILED = "failed"
    DELETED = "deleted"


ACTIVE_WORKTREE_STATUSES = frozenset(
    {
        WorktreeStatus.CREATING,
        WorktreeStatus.READY,
        WorktreeStatus.WORKING,
        WorktreeStatus.NEEDS_REVIEW,
        WorktreeStatus.MERGED,
    }
)


@dataclass
class Worktree(BaseAggregateRoot):
    """A git worktree with task metadata, terminals, and a port block."""

    workspace_id: UUID | None = None
    project_id: UUID | None = None
    devbox_id: UUID | None = None
    title: str = ""
    initial_prompt: str | None = None
    branch: str = ""
    base_branch: str = "main"
    base_sha: str | None = None
    head_sha: str | None = None
    path: str = ""
    slot: int = 0
    port_base: int = 30000
    status: WorktreeStatus = WorktreeStatus.CREATING
    is_primary: bool = False
    pr_number: int | None = None
    pr_url: str | None = None
    merged_at: datetime | None = None
    created_by: str = ""
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        """Return True for soft-deleted worktrees."""
        return self.deleted_at is not None


def slugify(value: str, *, max_length: int = 40) -> str:
    """Convert a worktree title into a safe branch and directory slug."""
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug[:max_length].rstrip("-")) or "worktree"


def branch_for_slug(slug: str) -> str:
    """Return the fallback branch name for a worktree slug."""
    return f"congruence/{slug}"


_BRANCH_CHARS = re.compile(r"^[A-Za-z0-9._/-]+$")


def is_valid_branch(branch: str) -> bool:
    """Return True for branch names git accepts and the UI can render safely."""
    if not branch or len(branch) > 200 or not _BRANCH_CHARS.match(branch):
        return False
    if branch.startswith(("-", "/", ".")) or branch.endswith(("/", ".", ".lock")):
        return False
    return ".." not in branch and "//" not in branch


def port_base_for_slot(slot: int, *, start: int, block: int) -> int:
    """Return the first port of a worktree's reserved block."""
    return start + (slot * block)


def worktree_path(project_slug: str, slug: str) -> str:
    """Return the absolute path of a worktree inside the project box."""
    return f"/workspaces/{project_slug}/wt/{slug}"


def project_root_path(project_slug: str) -> str:
    """Return the path of the project's root checkout (its primary worktree)."""
    return f"/workspaces/{project_slug}"
