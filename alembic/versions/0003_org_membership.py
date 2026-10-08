"""Organization membership: scope projects to the organizations their users belong to.

Revision ID: 0003_org_membership
Revises: 0002_host_custody

Additive only. Existing projects were attached to a shared "default-org" with no
members, so after this migration they are visible to nobody until a member is
added. Each new user gets a personal org on first use.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_org_membership"
down_revision = "0002_host_custody"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "congruence_organization_members",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(64),
            sa.ForeignKey("congruence_organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="owner"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_member"),
    )
    op.create_index(
        "ix_congruence_organization_members_organization_id",
        "congruence_organization_members",
        ["organization_id"],
    )
    op.create_index(
        "ix_congruence_organization_members_user_id",
        "congruence_organization_members",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_congruence_organization_members_user_id", table_name="congruence_organization_members")
    op.drop_index("ix_congruence_organization_members_organization_id", table_name="congruence_organization_members")
    op.drop_table("congruence_organization_members")
