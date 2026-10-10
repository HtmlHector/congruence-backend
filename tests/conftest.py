"""Pytest configuration and test fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Disable RLS and set test environment before importing app modules
os.environ["APP_ENVIRONMENT"] = "test"
os.environ["DATABASE_ENABLE_RLS"] = "false"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from src.adapters.api.dependencies import get_active_tenant, get_current_claims, get_current_user
from src.bootstrap.api import app
from src.contexts.identity.domain.entities import User
from src.infrastructure.auth.clerk import ClerkClaims
from src.infrastructure.database.base import Base
from src.infrastructure.database.session import get_db
from src.shared.tenant_context import TenantContext

# In-memory async SQLite engine for fast, isolated test execution
test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
)

test_session_factory = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

TEST_WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
async def setup_test_database() -> AsyncGenerator[None, None]:
    """Create all schema tables and seed test workspace before each test."""
    from uuid import UUID

    from src.contexts.identity.infrastructure.models import UserModel
    from src.contexts.workspace.domain.entities import WorkspaceRole
    from src.contexts.workspace.infrastructure.models import WorkspaceMemberModel, WorkspaceModel

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with test_session_factory() as session:
        user = UserModel(
            id=UUID(TEST_USER_ID),
            clerk_id="clerk_test_user_1",
            email="test@parabox.so",
            first_name="Test",
            last_name="User",
        )
        ws = WorkspaceModel(
            id=UUID(TEST_WORKSPACE_ID),
            name="Default Test Workspace",
            slug="default-test-workspace",
            stripe_customer_id="cus_test_mock_123",
        )
        session.add(user)
        session.add(ws)
        await session.flush()

        member = WorkspaceMemberModel(
            workspace_id=ws.id,
            user_id=user.id,
            role=WorkspaceRole.OWNER,
        )
        session.add(member)
        await session.commit()

    yield

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated test database session."""
    async with test_session_factory() as session:
        yield session


@pytest.fixture
def mock_claims() -> ClerkClaims:
    """Mock verified Clerk claims."""
    return ClerkClaims(
        user_id="clerk_test_user_1",
        org_id=TEST_WORKSPACE_ID,
        session_id="clerk_sess_1",
        email="test@parabox.so",
    )


@pytest.fixture
def mock_tenant() -> TenantContext:
    """Mock active workspace tenant context."""
    return TenantContext(
        workspace_id=TEST_WORKSPACE_ID,
        user_id="clerk_test_user_1",
        role="owner",
    )


@pytest.fixture
def mock_user() -> User:
    """Mock domain user entity."""
    from uuid import UUID

    return User(
        id=UUID(TEST_USER_ID),
        clerk_id="clerk_test_user_1",
        email="test@parabox.so",
        first_name="Test",
        last_name="User",
    )


@pytest.fixture
async def client(
    mock_claims: ClerkClaims,
    mock_tenant: TenantContext,
    mock_user: User,
) -> AsyncGenerator[AsyncClient, None]:
    """Provide async test HTTP client with dependencies overridden."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_claims] = lambda: mock_claims
    app.dependency_overrides[get_active_tenant] = lambda: mock_tenant
    app.dependency_overrides[get_current_user] = lambda: mock_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
