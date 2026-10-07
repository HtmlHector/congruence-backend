"""Host Custody: drop the credential vault, add host-reported harness auth.

Revision ID: 0002_host_custody
Revises: 0001

Destructive by design. `congruence_vault_secrets` held AES-256-GCM ciphertext of
user-supplied agent credentials. Dropping the table destroys every stored value
irreversibly, which is the correct outcome: we can no longer decrypt anything
without the removed VAULT_SECRET_KEY, so keeping the rows would leave
unreadable sensitive data sitting in a table that invites future use.

The replacement is `congruence_harness_auth`, which cannot hold a secret. Every
column is an enum, boolean, foreign key, or timestamp, because the control
plane has no key with which to encrypt one. See trd.md section 7.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_host_custody"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop the credential store. Irreversible; see module docstring.
    op.drop_table("congruence_vault_secrets")

    # 2. Mark callback-bridging services so they can be forced single-use and
    #    kept out of the preview UI.
    op.add_column(
        "congruence_services",
        sa.Column("service_kind", sa.String(length=32), nullable=False, server_default="app"),
    )
    op.add_column(
        "congruence_services",
        sa.Column("is_single_use", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "congruence_services",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 3. Host-reported harness connection state. No ciphertext column exists.
    op.create_table(
        "congruence_harness_auth",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("lane_id", sa.String(length=64), nullable=False),
        sa.Column("harness", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="disconnected"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["lane_id"], ["congruence_lanes.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("lane_id", "harness", name="uq_harness_auth_lane_harness"),
    )
    op.create_index(
        "ix_congruence_harness_auth_lane_id", "congruence_harness_auth", ["lane_id"]
    )


def downgrade() -> None:
    # The vault is intentionally NOT recreated. Reintroducing a table that can
    # hold credentials is the thing this revision exists to undo, and a
    # downgrade must not silently restore a security hole.
    op.drop_index("ix_congruence_harness_auth_lane_id", table_name="congruence_harness_auth")
    op.drop_table("congruence_harness_auth")

    op.drop_column("congruence_services", "expires_at")
    op.drop_column("congruence_services", "is_single_use")
    op.drop_column("congruence_services", "service_kind")
