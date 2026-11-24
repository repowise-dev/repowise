"""Tests for GET /api/repos/{id}/files — the browsable Files index."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode, Repository
from repowise.server.mcp_server.tool_context import enrichment
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

    # The Files table, file page, /graph/metrics, and MCP get_context must all
    # agree on the percentile rank among owned files (issue #5745).
    file_low = await client.get(f"/api/repos/{repo['id']}/files/src/low.py")
    assert file_low.status_code == 200
    assert file_low.json()["graph"]["pagerank_percentile"] == by_path["src/low.py"]["pagerank_pct"]

    file_high = await client.get(f"/api/repos/{repo['id']}/files/src/high.py")
    assert file_high.status_code == 200
    assert (
        file_high.json()["graph"]["pagerank_percentile"] == by_path["src/high.py"]["pagerank_pct"]
    )

    metrics_low = await client.get(f"/api/graph/{repo['id']}/metrics?node_id=src/low.py")
    assert metrics_low.status_code == 200
    assert metrics_low.json()["pagerank_percentile"] == by_path["src/low.py"]["pagerank_pct"]

    metrics_high = await client.get(f"/api/graph/{repo['id']}/metrics?node_id=src/high.py")
    assert metrics_high.status_code == 200
    assert metrics_high.json()["pagerank_percentile"] == by_path["src/high.py"]["pagerank_pct"]

    out_low: dict = {}
    out_high: dict = {}
    async with app.state.session_factory() as session:
        repository = await session.get(Repository, repo["id"])
        await enrichment._resolve_metrics(session, repository, "src/low.py", out_low)
        await enrichment._resolve_metrics(session, repository, "src/high.py", out_high)
    assert out_low["metrics"]["pagerank_percentile"] == by_path["src/low.py"]["pagerank_pct"]
    assert out_high["metrics"]["pagerank_percentile"] == by_path["src/high.py"]["pagerank_pct"]


@pytest.mark.asyncio
async def test_percentile_rank_single_file_repo(client: AsyncClient, app) -> None:
    """With exactly one owned file, values below that file get 0.0, not 100.0 (#5745)."""
    repo = await create_test_repo(client)
    await _insert_nodes(
        app.state.session_factory,
        repo["id"],
        [
            {"node_id": "src/only.py", "pagerank": 0.5},
            {"node_id": "external:react", "pagerank": 9.0},
        ],
    )

    resp = await client.get(f"/api/repos/{repo['id']}/files")
    by_path = {f["file_path"]: f for f in resp.json()["files"]}
    assert by_path["src/only.py"]["pagerank_pct"] == 100.0

    file_page = await client.get(f"/api/repos/{repo['id']}/files/src/only.py")
    assert file_page.status_code == 200
    assert file_page.json()["graph"]["pagerank_percentile"] == 100.0

    async with app.state.session_factory() as session:
        all_nodes = await crud.get_all_file_metrics(session, repo["id"])
        # A value below the lone file gets 0.0, at or above gets 100.0
        assert crud.percentile_rank(0.1, all_nodes) == 0.0
        assert crud.percentile_rank(0.5, all_nodes) == 100.0
        assert crud.percentile_rank(0.9, all_nodes) == 100.0
        assert await crud.get_pagerank_percentile(session, repo["id"], 0.1) == 0.0
        assert await crud.get_pagerank_percentile(session, repo["id"], 0.5) == 100.0
        assert await crud.get_pagerank_percentile(session, repo["id"], 0.9) == 100.0
