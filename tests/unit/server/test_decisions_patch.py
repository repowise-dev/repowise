"""Tests for PATCH /api/repos/{id}/decisions/{decision_id}.

Covers the expanded contract: clients may patch ``status`` alone, the
``affected_modules`` / ``affected_files`` linkage alone, or both in a single
request. Fields left as ``None`` must be preserved.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from tests.unit.server.conftest import create_test_repo


async def _seed_decision(session_factory, repo_id: str) -> str:
    async with get_session(session_factory) as session:
        rec = await crud.upsert_decision(
            session,
            repository_id=repo_id,
            title="Use SQLite for the dev DB",
            status="active",
            context="Local dev needs zero-config storage.",
            decision="SQLite via aiosqlite for all unit tests and CLI runs.",
            affected_modules=["packages/core"],
            affected_files=["packages/core/src/repowise/core/persistence/database.py"],
            source="cli",
        )
        return rec.id


@pytest.mark.asyncio
async def test_patch_decision_status_only(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"])

    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/{decision_id}",
        json={"status": "deprecated"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "deprecated"
    # Linkage preserved.
    assert body["affected_modules"] == ["packages/core"]
    assert body["affected_files"] == [
        "packages/core/src/repowise/core/persistence/database.py"
    ]


@pytest.mark.asyncio
async def test_patch_decision_modules_only(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"])

    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/{decision_id}",
        json={"affected_modules": ["packages/server", "packages/cli"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Status untouched.
    assert body["status"] == "active"
    assert body["affected_modules"] == ["packages/server", "packages/cli"]


@pytest.mark.asyncio
async def test_patch_decision_clear_files(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"])

    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/{decision_id}",
        json={"affected_files": []},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["affected_files"] == []
    # Modules untouched (None means preserve).
    assert body["affected_modules"] == ["packages/core"]


@pytest.mark.asyncio
async def test_patch_decision_status_and_modules(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"])

    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/{decision_id}",
        json={
            "status": "superseded",
            "superseded_by": "abc123",
            "affected_modules": ["packages/server"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "superseded"
    assert body["superseded_by"] == "abc123"
    assert body["affected_modules"] == ["packages/server"]


@pytest.mark.asyncio
async def test_patch_decision_not_found(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/missing",
        json={"status": "deprecated"},
    )
    assert resp.status_code == 404
