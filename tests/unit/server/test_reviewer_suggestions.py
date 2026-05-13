"""Tests for /api/repos/{id}/reviewer-suggestions."""

from __future__ import annotations

import json

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
            top_authors_json=json.dumps(
                [
                    {"name": "Alice", "email": "alice@example.com", "commit_count": 40},
                    {"name": "Bob", "email": "bob@example.com", "commit_count": 10},
                ]
            ),
            co_change_partners_json=json.dumps(
                [{"file_path": "src/utils.py", "co_change_count": 6}]
            ),
            is_hotspot=True,
            churn_percentile=0.9,
        )
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/utils.py",
            commit_count_total=20,
            commit_count_90d=5,
            primary_owner_name="Carol",
            primary_owner_email="carol@example.com",
            top_authors_json=json.dumps(
                [{"name": "Carol", "email": "carol@example.com", "commit_count": 20}]
            ),
            is_hotspot=False,
            churn_percentile=0.3,
        )


@pytest.mark.asyncio
async def test_reviewer_suggestions(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    resp = await client.get(
        f"/api/repos/{repo['id']}/reviewer-suggestions",
        params=[("paths", "src/main.py")],
    )
    assert resp.status_code == 200
    payload = resp.json()
    names = [s["name"] for s in payload["suggestions"]]
    # Alice (direct) should be ranked first; Carol comes in via co-change.
    assert names[0] == "Alice"
    assert "Carol" in names


@pytest.mark.asyncio
async def test_reviewer_suggestions_empty_paths(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(
        f"/api/repos/{repo['id']}/reviewer-suggestions",
        params=[("paths", "")],
    )
    # FastAPI accepts empty string as path; result should be empty.
    assert resp.status_code == 200
    assert resp.json()["suggestions"] == []
