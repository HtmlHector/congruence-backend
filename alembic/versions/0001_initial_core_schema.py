"""Initial core schema with multi-tenant RLS policies.

Revision ID: 0001
Revises:
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create Schemas
    op.execute("CREATE SCHEMA IF NOT EXISTS identity")
    op.execute("CREATE SCHEMA IF NOT EXISTS workspace")
    op.execute("CREATE SCHEMA IF NOT EXISTS subscription")
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")

    # 2. Identity Schema Tables
    op.create_table(
        "users",
        sa.Column(
            "user_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("supabase_user_id", sa.Text(), unique=True, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_workspace_id", sa.Text(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="identity",
    )
    op.create_index("idx_users_email", "users", ["email"], schema="identity")

    op.create_table(
        "roles",
        sa.Column(
            "role_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("workspace_id", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("permissions", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.UniqueConstraint("name", "workspace_id", name="uq_roles_name_workspace"),
        schema="identity",
    )

    op.create_table(
        "api_keys",
        sa.Column(
            "api_key_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), unique=True, nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_revoked", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="identity",
    )
    op.create_index("idx_api_keys_workspace", "api_keys", ["workspace_id"], schema="identity")

    # 3. Workspace Schema Tables
    op.create_table(
        "workspaces",
        sa.Column("workspace_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), unique=True, nullable=False),
        sa.Column("plan_tier", sa.Text(), server_default="FREE", nullable=False),
        sa.Column("stripe_customer_id", sa.Text(), unique=True, nullable=True),
        sa.Column(
            "settings", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="workspace",
    )

    op.create_table(
        "memberships",
        sa.Column(
            "membership_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_membership_user_workspace"),
        schema="workspace",
    )
    op.create_index("idx_memberships_ws", "memberships", ["workspace_id"], schema="workspace")

    op.create_table(
        "invitations",
        sa.Column(
            "invitation_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("invited_by", sa.UUID(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("token", sa.Text(), unique=True, nullable=False),
        sa.Column("status", sa.Text(), server_default="PENDING", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="workspace",
    )

    # 4. Subscription Schema Tables
    op.create_table(
        "subscriptions",
        sa.Column(
            "subscription_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("stripe_subscription_id", sa.Text(), unique=True, nullable=False),
        sa.Column("stripe_customer_id", sa.Text(), nullable=False),
        sa.Column("plan_tier", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="subscription",
    )
    op.create_index(
        "idx_subscriptions_workspace", "subscriptions", ["workspace_id"], schema="subscription"
    )

    # 5. Audit Schema Tables
    op.create_table(
        "audit_log",
        sa.Column(
            "log_id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("ip_address", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="audit",
    )
    op.create_index(
        "idx_audit_log_workspace", "audit_log", ["workspace_id", "created_at"], schema="audit"
    )

    # 6. Row Level Security Policies
    rls_tables = [
        ("workspace", "workspaces"),
        ("workspace", "memberships"),
        ("workspace", "invitations"),
        ("subscription", "subscriptions"),
        ("audit", "audit_log"),
    ]

    for schema, table in rls_tables:
        op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{schema}_{table} ON {schema}.{table}
            FOR ALL USING (
                workspace_id = current_setting('app.current_workspace_id', true)
            )
            """
        )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS audit CASCADE")
    op.execute("DROP SCHEMA IF EXISTS subscription CASCADE")
    op.execute("DROP SCHEMA IF EXISTS workspace CASCADE")
    op.execute("DROP SCHEMA IF EXISTS identity CASCADE")
