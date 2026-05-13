"""Tests for /api/repos/{id}/owners — contributor profile endpoints."""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/main.py",
            commit_count_total=50,
            commit_count_90d=20,
            commit_count_30d=5,
            primary_owner_name="Alice",
            primary_owner_email="alice@example.com",
            primary_owner_commit_pct=0.7,
            top_authors_json=json.dumps(
                [
                    {"name": "Alice", "email": "alice@example.com", "commit_count": 35},
                    {"name": "Bob", "email": "bob@example.com", "commit_count": 15},
                ]
            ),
            co_change_partners_json=json.dumps(
                [{"file_path": "src/utils.py", "co_change_count": 8}]
            ),
            is_hotspot=True,
            is_stable=False,
            churn_percentile=0.9,
            bus_factor=1,
            contributor_count=2,
            lines_added_90d=300,
            lines_deleted_90d=100,
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/utils.py",
            commit_count_total=10,
            commit_count_90d=2,
            primary_owner_name="Bob",
            primary_owner_email="bob@example.com",
            primary_owner_commit_pct=0.9,
            top_authors_json=json.dumps(
                [
                    {"name": "Bob", "email": "bob@example.com", "commit_count": 9},
                    {"name": "Alice", "email": "alice@example.com", "commit_count": 1},
                ]
            ),
            is_hotspot=False,
            churn_percentile=0.2,
            bus_factor=3,
            contributor_count=2,
        )


@pytest.mark.asyncio
async def test_list_owners(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/owners")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 2
    names = {item["name"] for item in payload["items"]}
    assert names == {"Alice", "Bob"}

    alice = next(i for i in payload["items"] if i["name"] == "Alice")
    assert alice["files_owned"] == 1
    assert alice["hotspots_owned"] == 1
    assert alice["bus_factor_risk_files"] == 1


@pytest.mark.asyncio
async def test_get_owner_profile(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    key = quote("alice@example.com", safe="")
    resp = await client.get(f"/api/repos/{repo['id']}/owners/{key}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Alice"
    assert data["files_owned"] == 1
    assert data["hotspots_owned"] == 1
    # Alice touched both files (she's in top_authors of utils too).
    paths = {f["file_path"] for f in data["top_files"]}
    assert "src/main.py" in paths
    assert "src/utils.py" in paths
    # Bob should appear as a co-author.
    coauthors = {c["name"] for c in data["co_authors"]}
    assert "Bob" in coauthors


@pytest.mark.asyncio
async def test_get_owner_profile_not_found(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/owners/ghost@nowhere")
    assert resp.status_code == 404
