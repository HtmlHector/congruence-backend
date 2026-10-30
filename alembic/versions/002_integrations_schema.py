"""Add integrations tables and RLS policies.

Revision ID: 002_integrations_schema
Revises: 001_initial_schema
Create Date: 2026-10-10 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '002_integrations_schema'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Enums
    integration_provider_enum = postgresql.ENUM(
        'GITHUB', 'SLACK', 'LINEAR', 'CUSTOM_WEBHOOK', 'CUSTOM_API_KEY',
        name='integration_provider',
        create_type=False
    )
    integration_provider_enum.create(op.get_bind(), checkfirst=True)

    credential_type_enum = postgresql.ENUM(
        'OAUTH_TOKEN', 'API_KEY', 'WEBHOOK_SECRET',
        name='credential_type',
        create_type=False
    )
    credential_type_enum.create(op.get_bind(), checkfirst=True)

    # 2. Integration Credentials table
    op.create_table(
        'integration_credentials',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', integration_provider_enum, nullable=False),
        sa.Column('credential_type', credential_type_enum, nullable=False, default='OAUTH_TOKEN'),
        sa.Column('encrypted_value', sa.Text(), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False, default=[]),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=False, default={}),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('workspace_id', 'provider', name='uq_workspace_provider_credential'),
    )
    op.create_index('ix_integration_credentials_workspace_id', 'integration_credentials', ['workspace_id'])
    op.create_index('ix_integration_credentials_provider', 'integration_credentials', ['provider'])

    # 3. Integration Configs table
    op.create_table(
        'integration_configs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', integration_provider_enum, nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, default=True),
        sa.Column('settings', sa.JSON(), nullable=False, default={}),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('workspace_id', 'provider', name='uq_workspace_provider_config'),
    )
    op.create_index('ix_integration_configs_workspace_id', 'integration_configs', ['workspace_id'])
    op.create_index('ix_integration_configs_provider', 'integration_configs', ['provider'])

    # 4. Webhook Events table
    op.create_table(
        'webhook_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=True),
        sa.Column('provider', integration_provider_enum, nullable=False),
        sa.Column('event_type', sa.String(255), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False, default={}),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_webhook_events_workspace_id', 'webhook_events', ['workspace_id'])
    op.create_index('ix_webhook_events_provider', 'webhook_events', ['provider'])

    # 5. Row-Level Security (RLS) policies for PostgreSQL
    op.execute("ALTER TABLE integration_credentials ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE integration_configs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;")

    op.execute("""
        CREATE POLICY integration_credentials_workspace_isolation ON integration_credentials
        FOR ALL USING (
            workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
        );
    """)

    op.execute("""
        CREATE POLICY integration_configs_workspace_isolation ON integration_configs
        FOR ALL USING (
            workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
        );
    """)

    op.execute("""
        CREATE POLICY webhook_events_workspace_isolation ON webhook_events
        FOR ALL USING (
            workspace_id IS NULL OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
        );
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS webhook_events_workspace_isolation ON webhook_events;")
    op.execute("DROP POLICY IF EXISTS integration_configs_workspace_isolation ON integration_configs;")
    op.execute("DROP POLICY IF EXISTS integration_credentials_workspace_isolation ON integration_credentials;")

    op.drop_table('webhook_events')
    op.drop_table('integration_configs')
    op.drop_table('integration_credentials')

    op.execute("DROP TYPE IF EXISTS credential_type;")
    op.execute("DROP TYPE IF EXISTS integration_provider;")
