"""Tests for GET /api/repos/{id}/files — the browsable Files index."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode
from tests.unit.server.conftest import create_test_repo


async def _insert_nodes(session_factory, repo_id: str, nodes: list[dict]) -> None:
    async with get_session(session_factory) as session:
        for spec in nodes:
            session.add(
                GraphNode(
                    repository_id=repo_id,
                    node_type="file",
                    language=spec.get("language", "python"),
                    pagerank=spec.get("pagerank", 0.0),
                    node_id=spec["node_id"],
                )
            )


@pytest.mark.asyncio
async def test_index_excludes_third_party_nodes(client: AsyncClient, app) -> None:
    """`external:` and `framework:` nodes are not files and must not be rows.

    They share `node_type == "file"` with real files, so they arrived in the
    index with no path, no LOC, no health and no coverage — a row of em-dashes
    whose only link 404s.
    """
    repo = await create_test_repo(client)
    await _insert_nodes(
        app.state.session_factory,
        repo["id"],
        [
            {"node_id": "src/main.py", "pagerank": 0.5},
            {"node_id": "external:react", "pagerank": 0.9},
            {"node_id": "framework:django", "pagerank": 0.8},
        ],
    )

    resp = await client.get(f"/api/repos/{repo['id']}/files")
    assert resp.status_code == 200
    payload = resp.json()

    assert [f["file_path"] for f in payload["files"]] == ["src/main.py"]
    assert payload["total"] == 1


@pytest.mark.asyncio
async def test_percentiles_rank_against_files_only(client: AsyncClient, app) -> None:
    """The excluded nodes must not skew where a real file lands.

    Filtering after the percentile would leave the top file ranked against
    imports the reader can never open — `external:react` outranks most of any
    repo, so every real file would report a percentile lower than its standing
    among files.
    """
    repo = await create_test_repo(client)
    await _insert_nodes(
        app.state.session_factory,
        repo["id"],
        [
            {"node_id": "src/low.py", "pagerank": 0.1},
            {"node_id": "src/high.py", "pagerank": 0.5},
            {"node_id": "external:react", "pagerank": 9.0},
        ],
    )

    resp = await client.get(f"/api/repos/{repo['id']}/files")
    by_path = {f["file_path"]: f for f in resp.json()["files"]}

    # Two files, so the ranking is the full 0-100 span rather than 0-50.
    assert by_path["src/low.py"]["pagerank_pct"] == 0.0
    assert by_path["src/high.py"]["pagerank_pct"] == 100.0
