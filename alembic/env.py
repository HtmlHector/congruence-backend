import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.config.settings import settings
from src.infrastructure.database.base import Base

# Ensure all models are imported so their metadata is registered
from src.contexts.identity.infrastructure.models import UserModel
from src.contexts.workspace.infrastructure.models import WorkspaceModel, WorkspaceMemberModel
from src.contexts.billing.infrastructure.models import SubscriptionModel
from src.contexts.audit.infrastructure.models import AuditLogModel
from src.contexts.projects.infrastructure.models import ProjectModel
from src.contexts.devbox.infrastructure.models import DevboxModel
from src.contexts.worktrees.infrastructure.models import WorktreeModel
from src.contexts.terminals.infrastructure.models import TerminalChunkModel, TerminalModel
from src.contexts.credentials.infrastructure.models import AgentCredentialModel
from src.contexts.environments.infrastructure.models import EnvVarModel
from src.contexts.usage.infrastructure.models import UsageRecordModel
from src.contexts.integrations.infrastructure.models import (
    IntegrationCredentialModel,
    IntegrationConfigModel,
    WebhookEventModel,
)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Overwrite sqlalchemy.url with application settings
config.set_main_option("sqlalchemy.url", settings.database.url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
