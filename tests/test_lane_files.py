import os
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.infrastructure.auth.dependencies import get_current_user
from src.infrastructure.auth.jwt import UserClaims
from src.infrastructure.database.base import Base
from src.infrastructure.database.session import get_db, get_db_session
from src.infrastructure.runner.registry import get_runner
from src.main import app

test_async_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSessionFactory = async_sessionmaker(test_async_engine, expire_on_commit=False)

TEST_USER = UserClaims(user_id="usr_owner", email="owner@example.com")


async def override_get_test_db():
    async with TestSessionFactory() as session:
        yield session


def override_get_current_user() -> UserClaims:
    return TEST_USER


@pytest.fixture(autouse=True)
async def prepare_database():
    orig_get_db = app.dependency_overrides.get(get_db)
    orig_get_db_session = app.dependency_overrides.get(get_db_session)
    orig_get_user = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_db] = override_get_test_db
    app.dependency_overrides[get_db_session] = override_get_test_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    congruence_tables = [t for t in Base.metadata.sorted_tables if t.name.startswith("congruence_")]
    async with test_async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=congruence_tables))
    yield
    async with test_async_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.drop_all(sync_conn, tables=congruence_tables))

    if orig_get_db is not None:
        app.dependency_overrides[get_db] = orig_get_db
    else:
        app.dependency_overrides.pop(get_db, None)

    if orig_get_db_session is not None:
        app.dependency_overrides[get_db_session] = orig_get_db_session
    else:
        app.dependency_overrides.pop(get_db_session, None)

    if orig_get_user is not None:
        app.dependency_overrides[get_current_user] = orig_get_user
    else:
        app.dependency_overrides.pop(get_current_user, None)

@pytest.mark.asyncio
async def test_lane_file_operations_and_listing():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create a project
        res = await client.post(
            "/projects",
            json={
                "name": "Files Test Project",
                "slug": f"files-test-{os.getpid()}",
                "repo_full_name": "test-org/files-test",
                "default_branch": "main",
                "visibility": "private",
            },
        )
        assert res.status_code == 201
        data = res.json()
        project_id = data["id"]
        pair_lane_id = data["pair_lane_id"]

        # 2. Write a file to the pair lane worktree
        write_res = await client.post(
            f"/projects/{project_id}/lanes/{pair_lane_id}/files",
            json={
                "path": "src/app.py",
                "content": "print('Hello Congruence')",
            },
        )
        assert write_res.status_code == 200

        # 3. Read the file back
        read_res = await client.get(
            f"/projects/{project_id}/lanes/{pair_lane_id}/file-content",
            params={"path": "src/app.py"},
        )
        assert read_res.status_code == 200
        assert read_res.json()["content"] == "print('Hello Congruence')"

        # 4. List the lane files
        list_res = await client.get(f"/projects/{project_id}/lanes/{pair_lane_id}/files")
        assert list_res.status_code == 200
        tree = list_res.json()["tree"]
        names = [node["name"] for node in tree]
        assert "README.md" in names or "src" in names
