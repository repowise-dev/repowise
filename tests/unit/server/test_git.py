"""Tests for git intelligence endpoints."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


async def _insert_git_metadata(session_factory, repo_id: str) -> None:
    """Insert test git metadata."""
    async with get_session(session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/main.py",
            commit_count_total=50,
            commit_count_90d=10,
            commit_count_30d=3,
            primary_owner_name="Alice",
            primary_owner_email="alice@example.com",
            primary_owner_commit_pct=0.6,
            top_authors_json=json.dumps([{"name": "Alice", "commits": 30}]),
            significant_commits_json=json.dumps([{"sha": "abc", "message": "init"}]),
            co_change_partners_json=json.dumps([{"file_path": "src/utils.py", "count": 5}]),
            is_hotspot=True,
            is_stable=False,
            churn_percentile=0.85,
            age_days=365,
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/utils.py",
            commit_count_total=20,
            commit_count_90d=0,
            commit_count_30d=0,
            primary_owner_name="Bob",
            primary_owner_email="bob@example.com",
            primary_owner_commit_pct=0.9,
            is_hotspot=False,
            is_stable=True,
            churn_percentile=0.2,
            age_days=200,
        )


@pytest.mark.asyncio
async def test_get_git_metadata(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/repos/{repo['id']}/git-metadata",
        params={"file_path": "src/main.py"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["file_path"] == "src/main.py"
    assert data["commit_count_total"] == 50
    assert data["is_hotspot"] is True
    assert data["primary_owner_name"] == "Alice"


@pytest.mark.asyncio
async def test_get_git_metadata_not_found(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(
        f"/api/repos/{repo['id']}/git-metadata",
        params={"file_path": "nonexistent.py"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_hotspots(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/hotspots")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1  # Only main.py is a hotspot
    assert data[0]["file_path"] == "src/main.py"
    assert data[0]["is_hotspot"] is True


@pytest.mark.asyncio
async def test_get_ownership(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/ownership")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    # Both files are under "src" module
    src_entry = next((e for e in data if e["module_path"] == "src"), None)
    assert src_entry is not None
    assert src_entry["file_count"] == 2


@pytest.mark.asyncio
async def test_get_co_changes(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/repos/{repo['id']}/co-changes",
        params={"file_path": "src/main.py", "min_count": 3},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["file_path"] == "src/main.py"
    assert len(data["co_change_partners"]) == 1
    assert data["co_change_partners"][0]["file_path"] == "src/utils.py"


@pytest.mark.asyncio
async def test_get_git_summary(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/git-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files"] == 2
    assert data["hotspot_count"] == 1
    assert data["stable_count"] == 1
    assert len(data["top_owners"]) == 2
