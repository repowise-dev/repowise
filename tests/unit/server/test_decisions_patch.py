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
from repowise.core.persistence.models import DecisionAlias
from tests.unit.server.conftest import create_test_repo


async def _seed_decision(
    session_factory, repo_id: str, title: str = "Use SQLite for the dev DB"
) -> str:
    async with get_session(session_factory) as session:
        rec = await crud.upsert_decision(
            session,
            repository_id=repo_id,
            title=title,
            status="active",
            context="Local dev needs zero-config storage.",
            decision=f"Use {title} for all unit tests and CLI runs.",
            affected_modules=["packages/core"],
            affected_files=["packages/core/src/repowise/core/persistence/database.py"],
            source="cli",
        )
        # ``active`` is a projection of an acceptance, so the seed has to
        # perform one; extraction and a bare upsert both land a candidate.
        await crud.accept_decision(session, rec, accepter="tester")
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
    # Modules follow the files they describe, so clearing one clears the
    # other: a record naming no file must not keep governing a whole subtree.
    assert body["affected_modules"] == []


@pytest.mark.asyncio
async def test_patch_decision_status_and_modules(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"])
    successor_id = await _seed_decision(
        app.state.session_factory, repo["id"], "Use PostgreSQL in production"
    )

    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/{decision_id}",
        json={
            "status": "superseded",
            "superseded_by": successor_id,
            "affected_modules": ["packages/server"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "superseded"
    assert body["superseded_by"] == successor_id
    assert body["affected_modules"] == ["packages/server"]


@pytest.mark.asyncio
async def test_patch_decision_rejects_unknown_successor(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"])

    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/{decision_id}",
        json={"status": "superseded", "superseded_by": "no-such-decision"},
    )

    assert resp.status_code == 404
    current = await client.get(f"/api/repos/{repo['id']}/decisions/{decision_id}")
    assert current.status_code == 200
    assert current.json()["status"] == "active"
    assert current.json()["superseded_by"] is None


@pytest.mark.asyncio
async def test_patch_decision_rejects_cross_repo_successor(client: AsyncClient, app) -> None:
    repo_a = await create_test_repo(client)
    repo_b = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo_a["id"])
    foreign_id = await _seed_decision(
        app.state.session_factory, repo_b["id"], "Use PostgreSQL in the other repo"
    )

    resp = await client.patch(
        f"/api/repos/{repo_a['id']}/decisions/{decision_id}",
        json={"status": "superseded", "superseded_by": foreign_id},
    )

    assert resp.status_code == 404
    current = await client.get(f"/api/repos/{repo_a['id']}/decisions/{decision_id}")
    assert current.status_code == 200
    assert current.json()["status"] == "active"
    assert current.json()["superseded_by"] is None


@pytest.mark.asyncio
async def test_patch_decision_resolves_same_repo_successor_alias(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    sf = app.state.session_factory
    successor_id = await _seed_decision(sf, repo["id"], "Use the successor decision")
    decision_id = await _seed_decision(sf, repo["id"], "Retire this decision")
    retired_id = "f" * 32
    async with get_session(sf) as session:
        session.add(
            DecisionAlias(
                alias_id=retired_id,
                repository_id=repo["id"],
                decision_id=successor_id,
                reason="superseded",
            )
        )
        await session.commit()

    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/{decision_id}",
        json={"status": "superseded", "superseded_by": retired_id},
    )

    assert resp.status_code == 200
    assert resp.json()["superseded_by"] == successor_id


@pytest.mark.asyncio
async def test_patch_decision_rejects_self_successor(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"])

    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/{decision_id}",
        json={"status": "superseded", "superseded_by": decision_id},
    )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_decision_not_found(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.patch(
        f"/api/repos/{repo['id']}/decisions/missing",
        json={"status": "deprecated"},
    )
    assert resp.status_code == 404
