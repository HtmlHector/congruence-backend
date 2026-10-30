"""GitLab and Bitbucket integration support.

Revision ID: 005_gitlab_bitbucket
Revises: 004_worktree_primary
Create Date: 2026-10-24 15:00:00.000000

GitLab and Bitbucket OAuth access tokens expire, so the credential row also
stores an encrypted refresh token to rotate them without user involvement.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005_gitlab_bitbucket"
down_revision: str | None = "004_worktree_primary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PostgreSQL will not add an enum value inside the migration transaction on
    # older servers, and the new value is unusable until it is committed.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE integration_provider ADD VALUE IF NOT EXISTS 'GITLAB'")
        op.execute("ALTER TYPE integration_provider ADD VALUE IF NOT EXISTS 'BITBUCKET'")

    op.add_column(
        "integration_credentials",
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("integration_credentials", "refresh_token_encrypted")
    # PostgreSQL cannot remove enum values, so GITLAB/BITBUCKET remain defined.
