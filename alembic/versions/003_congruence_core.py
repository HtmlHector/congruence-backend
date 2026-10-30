"""Congruence core: devboxes, worktrees, terminals, credentials, env, usage.

Revision ID: 003_congruence_core
Revises: 002_integrations_schema
Create Date: 2026-10-23 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_congruence_core"
down_revision: Union[str, None] = "002_integrations_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(name: str, values: list[str]) -> postgresql.ENUM:
    enum = postgresql.ENUM(*values, name=name, create_type=False)
    enum.create(op.get_bind(), checkfirst=True)
    return enum


def upgrade() -> None:
    setup_status = _enum("setup_status", ["PENDING", "RUNNING", "READY", "FAILED"])
    devbox_status = _enum(
        "devbox_status",
        ["PROVISIONING", "AWAKE", "PAUSING", "PAUSED", "WAKING", "ERROR", "DESTROYED"],
    )
    worktree_status = _enum(
        "worktree_status",
        ["CREATING", "READY", "WORKING", "NEEDS_REVIEW", "MERGED", "FAILED", "DELETED"],
    )
    terminal_kind = _enum("terminal_kind", ["SHELL", "AGENT", "LOGIN"])
    terminal_status = _enum(
        "terminal_status", ["STARTING", "OPEN", "DETACHED", "LOST", "CLOSED"]
    )
    credential_provider = _enum("credential_provider", ["ANTHROPIC", "OPENAI"])
    credential_kind = _enum("credential_kind", ["API_KEY", "OAUTH"])
    credential_status = _enum("credential_status", ["ACTIVE", "NEEDS_RECONNECT", "REVOKED"])
    env_scope = _enum("env_scope", ["WORKSPACE", "PROJECT"])

    # --- projects: repository binding and setup state ---
    op.add_column("projects", sa.Column("repository_id", sa.String(255), nullable=True))
    op.add_column(
        "projects",
        sa.Column("repo_provider", sa.String(32), nullable=False, server_default="github"),
    )
    op.add_column(
        "projects",
        sa.Column("default_branch", sa.String(255), nullable=False, server_default="main"),
    )
    op.add_column(
        "projects", sa.Column("root_path", sa.String(1024), nullable=False, server_default="")
    )
    op.add_column("projects", sa.Column("setup_command", sa.Text(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("setup_status", setup_status, nullable=False, server_default="PENDING"),
    )
    op.add_column("projects", sa.Column("setup_log_ref", sa.String(1024), nullable=True))
    op.add_column("projects", sa.Column("base_image_ref", sa.String(512), nullable=True))
    op.add_column("projects", sa.Column("created_by", sa.String(255), nullable=True))
    op.create_index("ix_projects_repository_id", "projects", ["repository_id"])
    op.create_index("ix_projects_setup_status", "projects", ["setup_status"])

    # --- devboxes ---
    op.create_table(
        "devboxes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_box_id", sa.String(255), nullable=True),
        sa.Column("region", sa.String(64), nullable=False, server_default="local"),
        sa.Column("status", devbox_status, nullable=False, server_default="PROVISIONING"),
        sa.Column("image_ref", sa.String(512), nullable=False),
        sa.Column("cpu", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("memory_mb", sa.Integer(), nullable=False, server_default="4096"),
        sa.Column("disk_gb", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("awake_seconds_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_devboxes_workspace_id", "devboxes", ["workspace_id"])
    op.create_index("ix_devboxes_project_id", "devboxes", ["project_id"])
    op.create_index("ix_devboxes_provider_box_id", "devboxes", ["provider_box_id"])
    op.create_index("ix_devboxes_status", "devboxes", ["status"])
    op.create_index(
        "devboxes_one_live_per_project",
        "devboxes",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('DESTROYED', 'ERROR')"),
    )

    # --- worktrees ---
    op.create_table(
        "worktrees",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "devbox_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devboxes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("initial_prompt", sa.Text(), nullable=True),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("base_branch", sa.String(255), nullable=False),
        sa.Column("base_sha", sa.String(64), nullable=True),
        sa.Column("head_sha", sa.String(64), nullable=True),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("slot", sa.SmallInteger(), nullable=False),
        sa.Column("port_base", sa.Integer(), nullable=False),
        sa.Column("status", worktree_status, nullable=False, server_default="CREATING"),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(1024), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_worktrees_workspace_id", "worktrees", ["workspace_id"])
    op.create_index("ix_worktrees_project_id", "worktrees", ["project_id"])
    op.create_index("ix_worktrees_devbox_id", "worktrees", ["devbox_id"])
    op.create_index("ix_worktrees_status", "worktrees", ["status"])
    op.create_index(
        "worktrees_branch_live",
        "worktrees",
        ["project_id", "branch"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "worktrees_path_live",
        "worktrees",
        ["project_id", "path"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "worktrees_slot_live",
        "worktrees",
        ["project_id", "slot"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- agent credentials ---
    op.create_table(
        "agent_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("provider", credential_provider, nullable=False),
        sa.Column("kind", credential_kind, nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("status", credential_status, nullable=False, server_default="ACTIVE"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_credentials_workspace_id", "agent_credentials", ["workspace_id"])
    op.create_index("ix_agent_credentials_user_id", "agent_credentials", ["user_id"])
    op.create_index("ix_agent_credentials_status", "agent_credentials", ["status"])
    op.create_index(
        "one_active_credential",
        "agent_credentials",
        ["user_id", "workspace_id", "provider"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # --- terminals ---
    op.create_table(
        "terminals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "worktree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("worktrees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "devbox_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devboxes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tmux_session", sa.String(255), nullable=True),
        sa.Column("kind", terminal_kind, nullable=False, server_default="SHELL"),
        sa.Column("agent", sa.String(64), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("status", terminal_status, nullable=False, server_default="STARTING"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column(
            "credential_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recording_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("cols", sa.SmallInteger(), nullable=False, server_default="120"),
        sa.Column("rows", sa.SmallInteger(), nullable=False, server_default="32"),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("scrollback_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind <> 'LOGIN' OR recording_enabled = false", name="login_never_recorded"
        ),
    )
    op.create_index("ix_terminals_workspace_id", "terminals", ["workspace_id"])
    op.create_index("ix_terminals_worktree_id", "terminals", ["worktree_id"])
    op.create_index("ix_terminals_devbox_id", "terminals", ["devbox_id"])
    op.create_index("ix_terminals_status", "terminals", ["status"])
    op.create_index("terminals_tmux", "terminals", ["devbox_id", "tmux_session"], unique=True)

    op.create_table(
        "terminal_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "terminal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("terminals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_terminal_chunks_terminal_id", "terminal_chunks", ["terminal_id"])
    op.create_index(
        "terminal_chunks_terminal_seq", "terminal_chunks", ["terminal_id", "seq"], unique=True
    )

    # --- env vars ---
    op.create_table(
        "env_vars",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", env_scope, nullable=False, server_default="PROJECT"),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value_ciphertext", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope = 'PROJECT') = (project_id IS NOT NULL)", name="env_scope_project_check"
        ),
    )
    op.create_index("ix_env_vars_workspace_id", "env_vars", ["workspace_id"])
    op.create_index("env_vars_lookup", "env_vars", ["workspace_id", "scope", "project_id", "key"])

    # --- usage ---
    op.create_table(
        "usage_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("devbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False, server_default="box_awake_seconds"),
        sa.Column("quantity", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_to_stripe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_records_workspace_id", "usage_records", ["workspace_id"])
    op.create_index(
        "usage_no_double_count",
        "usage_records",
        ["devbox_id", "period_start", "period_end"],
        unique=True,
    )

    # --- RLS ---
    for table in (
        "devboxes",
        "worktrees",
        "terminals",
        "env_vars",
        "usage_records",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_workspace_isolation ON {table}
            USING (workspace_id = current_setting('app.current_workspace_id', true)::uuid)
            WITH CHECK (workspace_id = current_setting('app.current_workspace_id', true)::uuid);
            """
        )

    # Credentials are private to a user *and* a workspace.
    op.execute("ALTER TABLE agent_credentials ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE agent_credentials FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY agent_credentials_owner_only ON agent_credentials
        USING (
            workspace_id = current_setting('app.current_workspace_id', true)::uuid
            AND user_id = current_setting('app.current_user_id', true)
        )
        WITH CHECK (
            workspace_id = current_setting('app.current_workspace_id', true)::uuid
            AND user_id = current_setting('app.current_user_id', true)
        );
        """
    )

    # Transcript chunks inherit tenancy from their terminal.
    op.execute("ALTER TABLE terminal_chunks ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE terminal_chunks FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY terminal_chunks_workspace_isolation ON terminal_chunks
        USING (
            EXISTS (
                SELECT 1 FROM terminals t
                WHERE t.id = terminal_chunks.terminal_id
                  AND t.workspace_id = current_setting('app.current_workspace_id', true)::uuid
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM terminals t
                WHERE t.id = terminal_chunks.terminal_id
                  AND t.workspace_id = current_setting('app.current_workspace_id', true)::uuid
            )
        );
        """
    )


def downgrade() -> None:
    for table in (
        "devboxes",
        "worktrees",
        "terminals",
        "env_vars",
        "usage_records",
        "terminal_chunks",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table};")
    op.execute("DROP POLICY IF EXISTS agent_credentials_owner_only ON agent_credentials;")

    op.drop_table("usage_records")
    op.drop_table("env_vars")
    op.drop_table("terminal_chunks")
    op.drop_table("terminals")
    op.drop_index("one_active_credential", table_name="agent_credentials")
    op.drop_table("agent_credentials")
    op.drop_table("worktrees")
    op.drop_table("devboxes")

    op.drop_index("ix_projects_setup_status", table_name="projects")
    op.drop_index("ix_projects_repository_id", table_name="projects")
    for column in (
        "created_by",
        "base_image_ref",
        "setup_log_ref",
        "setup_status",
        "setup_command",
        "root_path",
        "default_branch",
        "repo_provider",
        "repository_id",
    ):
        op.drop_column("projects", column)

    for enum_name in (
        "env_scope",
        "credential_status",
        "credential_kind",
        "credential_provider",
        "terminal_status",
        "terminal_kind",
        "worktree_status",
        "devbox_status",
        "setup_status",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name};")
