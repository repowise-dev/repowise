"""History-only git rows (docs, config) carry counts but no ownership signal.

A README written by one person must not make them a file owner, a silo or a
bus-factor risk, nor pull a module's bus factor or hotspot share down.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.server.services.knowledge_map import compute_knowledge_map
from tests.unit.server.conftest import create_test_repo
from tests.unit.server.test_modules_health import _seed


async def _add_doc(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/README.md",
            history_only=True,
            commit_count_total=3,
            primary_owner_name="Carol",
            primary_owner_email="carol@example.com",
            primary_owner_commit_pct=1.0,
            top_authors_json=json.dumps(
                [{"name": "Carol", "email": "carol@example.com", "commit_count": 3}]
            ),
            bus_factor=1,
            churn_percentile=0.0,
        )


async def _snapshot(client: AsyncClient, session_factory, repo_id: str) -> dict:
    base = f"/api/repos/{repo_id}"
    out = {}
    for key, path in {
        "owners": "/owners",
        "modules": "/modules/health",
        "ownership": "/ownership?granularity=module",
        "summary": "/git-summary",
    }.items():
        resp = await client.get(base + path)
        assert resp.status_code == 200, (path, resp.text)
        out[key] = resp.json()
    async with get_session(session_factory) as session:
        out["knowledge"] = await compute_knowledge_map(session, repo_id)
    return out


@pytest.mark.asyncio
async def test_doc_rows_leave_ownership_and_module_rollups_unchanged(
    client: AsyncClient, app
) -> None:
    plain = await create_test_repo(client)
    await _seed(app.state.session_factory, plain["id"])
    with_doc = await create_test_repo(client)
    await _seed(app.state.session_factory, with_doc["id"])
    await _add_doc(app.state.session_factory, with_doc["id"])

    want = await _snapshot(client, app.state.session_factory, plain["id"])
    got = await _snapshot(client, app.state.session_factory, with_doc["id"])

    assert {o["name"] for o in got["owners"]["items"]} == {"Alice"}
    assert got["owners"]["items"] == want["owners"]["items"]
    assert got["modules"]["items"] == want["modules"]["items"]
    assert got["ownership"]["items"] == want["ownership"]["items"]
    assert got["summary"] == want["summary"]
    for key in ("top_owners", "knowledge_silos"):
        assert got["knowledge"][key] == want["knowledge"][key]
