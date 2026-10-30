"""Initial schema with users, workspaces, billing, audit, projects, and RLS.

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-10-10 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Users table
    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('clerk_id', sa.String(255), nullable=False, unique=True),
        sa.Column('email', sa.String(255), nullable=False, unique=True),
        sa.Column('first_name', sa.String(100), nullable=True),
        sa.Column('last_name', sa.String(100), nullable=True),
        sa.Column('avatar_url', sa.String(1024), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_users_clerk_id', 'users', ['clerk_id'])
    op.create_index('ix_users_email', 'users', ['email'])

    # 2. Workspaces table
    op.create_table(
        'workspaces',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('slug', sa.String(255), nullable=False, unique=True),
        sa.Column('stripe_customer_id', sa.String(255), nullable=True, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_workspaces_slug', 'workspaces', ['slug'])

    # 3. Workspace members table
    workspace_role_enum = postgresql.ENUM('OWNER', 'ADMIN', 'MEMBER', name='workspace_role', create_type=False)
    workspace_role_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'workspace_members',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', workspace_role_enum, nullable=False, default='MEMBER'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('workspace_id', 'user_id', name='uq_workspace_member'),
    )
    op.create_index('ix_workspace_members_workspace_id', 'workspace_members', ['workspace_id'])
    op.create_index('ix_workspace_members_user_id', 'workspace_members', ['user_id'])

    # 4. Subscriptions table
    plan_tier_enum = postgresql.ENUM('FREE', 'PRO', 'ENTERPRISE', name='plan_tier', create_type=False)
    plan_tier_enum.create(op.get_bind(), checkfirst=True)

    sub_status_enum = postgresql.ENUM('ACTIVE', 'TRIALING', 'PAST_DUE', 'CANCELED', 'INCOMPLETE', name='subscription_status', create_type=False)
    sub_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'subscriptions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('stripe_subscription_id', sa.String(255), nullable=True, unique=True),
        sa.Column('stripe_customer_id', sa.String(255), nullable=True),
        sa.Column('stripe_price_id', sa.String(255), nullable=True),
        sa.Column('tier', plan_tier_enum, nullable=False, default='FREE'),
        sa.Column('status', sub_status_enum, nullable=False, default='ACTIVE'),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False, default=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_subscriptions_workspace_id', 'subscriptions', ['workspace_id'])

    # 5. Audit logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_id', sa.String(255), nullable=False),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('resource_type', sa.String(100), nullable=False),
        sa.Column('resource_id', sa.String(255), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False, default={}),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_audit_logs_workspace_id', 'audit_logs', ['workspace_id'])
    op.create_index('ix_audit_logs_actor_id', 'audit_logs', ['actor_id'])

    # 6. Projects sample table
    project_status_enum = postgresql.ENUM('DRAFT', 'ACTIVE', 'ARCHIVED', name='project_status', create_type=False)
    project_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'projects',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', project_status_enum, nullable=False, default='DRAFT'),
        sa.Column('metadata', sa.JSON(), nullable=False, default={}),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_projects_workspace_id', 'projects', ['workspace_id'])
    op.create_index('ix_projects_status', 'projects', ['status'])

    # 7. Row-Level Security (RLS) policies for PostgreSQL
    # Enable RLS on tenant-scoped tables
    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;")

    # Create policies that enforce current_setting('app.current_workspace_id', true)
    op.execute("""
        CREATE POLICY projects_workspace_isolation ON projects
        FOR ALL USING (
            workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
        );
    """)

    op.execute("""
        CREATE POLICY audit_logs_workspace_isolation ON audit_logs
        FOR ALL USING (
            workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
        );
    """)

    op.execute("""
        CREATE POLICY subscriptions_workspace_isolation ON subscriptions
        FOR ALL USING (
            workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
        );
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS subscriptions_workspace_isolation ON subscriptions;")
    op.execute("DROP POLICY IF EXISTS audit_logs_workspace_isolation ON audit_logs;")
    op.execute("DROP POLICY IF EXISTS projects_workspace_isolation ON projects;")

    op.drop_table('projects')
    op.drop_table('audit_logs')
    op.drop_table('subscriptions')
    op.drop_table('workspace_members')
    op.drop_table('workspaces')
    op.drop_table('users')

    op.execute("DROP TYPE IF EXISTS project_status;")
    op.execute("DROP TYPE IF EXISTS subscription_status;")
    op.execute("DROP TYPE IF EXISTS plan_tier;")
    op.execute("DROP TYPE IF EXISTS workspace_role;")
