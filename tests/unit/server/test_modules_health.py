"""Tests for /api/repos/{id}/modules/health."""

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
            primary_owner_name="Alice",
            primary_owner_email="alice@example.com",
            primary_owner_commit_pct=0.9,
            top_authors_json=json.dumps(
                [{"name": "Alice", "email": "alice@example.com", "commit_count": 45}]
            ),
            is_hotspot=True,
            churn_percentile=0.9,
            bus_factor=1,
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/utils.py",
            commit_count_total=10,
            primary_owner_name="Alice",
            primary_owner_email="alice@example.com",
            primary_owner_commit_pct=0.6,
            top_authors_json=json.dumps(
                [{"name": "Alice", "email": "alice@example.com", "commit_count": 6}]
            ),
            is_hotspot=False,
            churn_percentile=0.3,
            bus_factor=2,
        )


@pytest.mark.asyncio
async def test_list_module_health(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/modules/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] >= 1
    src = next(m for m in payload["items"] if m["module_path"] == "src")
    assert src["file_count"] == 2
    assert src["hotspot_count"] == 1
    assert 0 <= src["health_score"] <= 100


@pytest.mark.asyncio
async def test_get_module_health_detail(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/modules/health/{quote('src')}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["module_path"] == "src"
    assert data["file_count"] == 2
    assert any(o["name"] == "Alice" for o in data["owners"])


@pytest.mark.asyncio
async def test_get_module_health_nested_path_falls_back_to_parent(
    client: AsyncClient, app
) -> None:
    """Path-shaped wiki module ids (``src/api``) roll up to the parent module.

    Curated module pages key on the module's directory path; health rollups
    aggregate by top-level dir, so a nested module path resolves through the
    exact → single-file → parent fallback chain to its top-level aggregate.
    """
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/modules/health/{quote('src/api')}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["module_path"] == "src"
    assert data["file_count"] == 2
