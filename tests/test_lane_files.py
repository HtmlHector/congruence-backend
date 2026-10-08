import pytest
import os
import tempfile
from httpx import ASGITransport, AsyncClient
from src.main import app
from src.infrastructure.runner.registry import get_runner

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
