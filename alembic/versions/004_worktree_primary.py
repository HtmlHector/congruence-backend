"""Worktree primary checkout.

Revision ID: 004_worktree_primary
Revises: 003_congruence_core
Create Date: 2026-10-24 12:00:00.000000

Adds the ``is_primary`` flag: every provisioned project records its root
checkout as a worktree so the UI can list it beside task worktrees. Existing
projects pick one up the next time they provision or create a worktree.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004_worktree_primary"
down_revision: str | None = "003_congruence_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "worktrees",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "worktrees_one_primary",
        "worktrees",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_primary AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("worktrees_one_primary", table_name="worktrees")
    op.drop_column("worktrees", "is_primary")
