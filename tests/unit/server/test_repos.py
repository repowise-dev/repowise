"""Tests for /api/repos endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.unit.server.conftest import create_test_repo


@pytest.mark.asyncio
async def test_create_repo(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/repos",
        json={
            "name": "my-repo",
            "local_path": "/tmp/my-repo",
            "url": "https://github.com/example/my-repo",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "my-repo"
    assert data["local_path"] == "/tmp/my-repo"
    assert data["url"] == "https://github.com/example/my-repo"
    assert data["default_branch"] == "main"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_repos_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_repos_with_data(client: AsyncClient) -> None:
    await create_test_repo(client)
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "test-repo"


@pytest.mark.asyncio
async def test_get_repo_by_id(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "test-repo"


@pytest.mark.asyncio
async def test_get_repo_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/repos/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_repo(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.patch(
        f"/api/repos/{repo['id']}",
        json={"name": "updated-name"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "updated-name"


@pytest.mark.asyncio
async def test_sync_repo_returns_202(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    with patch("repowise.server.routers.repos.execute_job", new_callable=AsyncMock):
        resp = await client.post(f"/api/repos/{repo['id']}/sync")
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "accepted"


@pytest.mark.asyncio
async def test_sync_repo_not_found(client: AsyncClient) -> None:
    resp = await client.post("/api/repos/nonexistent/sync")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_full_resync_returns_202(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    with patch("repowise.server.routers.repos.execute_job", new_callable=AsyncMock):
        resp = await client.post(f"/api/repos/{repo['id']}/full-resync")
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data


@pytest.mark.asyncio
async def test_sync_duplicate_returns_409(client: AsyncClient) -> None:
    """A second sync while one is pending/running returns 409."""
    repo = await create_test_repo(client)

    # Mock execute_job to prevent actual background pipeline run during tests
    with patch("repowise.server.routers.repos.execute_job", new_callable=AsyncMock):
        resp1 = await client.post(f"/api/repos/{repo['id']}/sync")
        assert resp1.status_code == 202

        # Second sync should be rejected (job is still pending/running)
        resp2 = await client.post(f"/api/repos/{repo['id']}/sync")
        assert resp2.status_code == 409
        assert "already in progress" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_full_resync_duplicate_returns_409(client: AsyncClient) -> None:
    """A second full-resync while one is pending/running returns 409."""
    repo = await create_test_repo(client)

    with patch("repowise.server.routers.repos.execute_job", new_callable=AsyncMock):
        resp1 = await client.post(f"/api/repos/{repo['id']}/full-resync")
        assert resp1.status_code == 202

        resp2 = await client.post(f"/api/repos/{repo['id']}/full-resync")
        assert resp2.status_code == 409
