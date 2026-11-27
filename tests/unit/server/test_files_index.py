"""Tests for GET /api/repos/{id}/files — the browsable Files index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphMetric, GraphNode
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


async def _insert_metrics(session_factory, repo_id: str, metrics: list[dict]) -> None:
    """Stored ``graph_metrics`` snapshot rows, as the indexer materializes."""
    async with get_session(session_factory) as session:
        for spec in metrics:
            session.add(
                GraphMetric(
                    repository_id=repo_id,
                    node_id=spec["node_id"],
                    pagerank=spec.get("pagerank", 0.0),
                    betweenness=0.0,
                    community_id=0,
                    in_degree=spec.get("in_degree", 0),
                    out_degree=spec.get("out_degree", 0),
                )
            )


async def _insert_edges(session_factory, repo_id: str, edges: list[tuple[str, str]]) -> None:
    async with get_session(session_factory) as session:
        for source, target in edges:
            session.add(
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id=source,
                    target_node_id=target,
                    edge_type="imports",
                    confidence=1.0,
                )
            )


async def _insert_git_metadata(session_factory, repo_id: str) -> None:
    """``src/a.py`` whose co-change partners straddle the exclude boundary."""
    async with get_session(session_factory) as session:
        await crud.upsert_git_metadata(
            session,
            repository_id=repo_id,
            file_path="src/a.py",
            commit_count_total=10,
            co_change_partners_json=json.dumps(
                [
                    {"file_path": "gen/out.py", "co_change_count": 4, "frequency": 4},
                    {"file_path": "src/b.py", "co_change_count": 3, "frequency": 3},
                ]
            ),
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


@pytest.mark.asyncio
async def test_index_excludes_gitignored_files(client: AsyncClient, app, tmp_path: Path) -> None:
    """A file the repo rules exclude is absent from the index and its counts.

    The repro from #6478: `.gitignore` holds `gen/`; nodes `src/a.py`,
    `src/b.py`, `gen/out.py`; edge `gen/out.py -> src/a.py` and edge
    `src/b.py -> gen/out.py`. The stored snapshot still counts those edges,
    so the index must drop the excluded row before `total`, `languages` and
    the percentile, and ignore edges into/out of the excluded file in the
    kept rows' degrees.
    """
    repo = await create_test_repo(client, tmp_path)
    (tmp_path / "test-repo" / ".gitignore").write_text("gen/\n", encoding="utf-8")
    await _insert_nodes(
        app.state.session_factory,
        repo["id"],
        [
            {"node_id": "src/a.py", "pagerank": 0.5},
            {"node_id": "src/b.py", "pagerank": 0.1},
            {"node_id": "gen/out.py", "pagerank": 0.9},
        ],
    )
    await _insert_metrics(
        app.state.session_factory,
        repo["id"],
        [
            {"node_id": "src/a.py", "in_degree": 1, "out_degree": 0},
            {"node_id": "src/b.py", "in_degree": 0, "out_degree": 1},
            {"node_id": "gen/out.py", "in_degree": 0, "out_degree": 1},
        ],
    )
    await _insert_edges(
        app.state.session_factory,
        repo["id"],
        [("gen/out.py", "src/a.py"), ("src/b.py", "gen/out.py")],
    )

    resp = await client.get(f"/api/repos/{repo['id']}/files")
    assert resp.status_code == 200
    payload = resp.json()

    assert [f["file_path"] for f in payload["files"]] == ["src/a.py", "src/b.py"]
    assert payload["total"] == 2
    assert payload["languages"] == [{"language": "python", "count": 2}]
    by_path = {f["file_path"]: f for f in payload["files"]}
    # Ranked against the kept files only: `gen/out.py` at 0.9 must not push
    # `src/a.py` down to 50.0.
    assert by_path["src/a.py"]["pagerank_pct"] == 100.0
    assert by_path["src/b.py"]["pagerank_pct"] == 0.0
    # The stored degrees counted the edges to the excluded file; both
    # directions are ignored now.
    assert by_path["src/a.py"]["in_degree"] == 0
    assert by_path["src/b.py"]["out_degree"] == 0


@pytest.mark.asyncio
async def test_index_unchanged_when_nothing_excluded(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    """Without exclude rules the index serves the stored snapshot verbatim.

    Same graph as the exclusion test, minus the `.gitignore`: every row is
    present and the stored degrees — including the edges the other test
    drops — flow through untouched.
    """
    repo = await create_test_repo(client, tmp_path)
    await _insert_nodes(
        app.state.session_factory,
        repo["id"],
        [
            {"node_id": "src/a.py", "pagerank": 0.5},
            {"node_id": "src/b.py", "pagerank": 0.1},
            {"node_id": "gen/out.py", "pagerank": 0.9},
        ],
    )
    await _insert_metrics(
        app.state.session_factory,
        repo["id"],
        [
            {"node_id": "src/a.py", "in_degree": 1, "out_degree": 0},
            {"node_id": "src/b.py", "in_degree": 0, "out_degree": 1},
            {"node_id": "gen/out.py", "in_degree": 0, "out_degree": 1},
        ],
    )
    await _insert_edges(
        app.state.session_factory,
        repo["id"],
        [("gen/out.py", "src/a.py"), ("src/b.py", "gen/out.py")],
    )

    resp = await client.get(f"/api/repos/{repo['id']}/files")
    assert resp.status_code == 200
    payload = resp.json()

    assert [f["file_path"] for f in payload["files"]] == [
        "src/a.py",
        "src/b.py",
        "gen/out.py",
    ]
    assert payload["total"] == 3
    assert payload["languages"] == [{"language": "python", "count": 3}]
    by_path = {f["file_path"]: f for f in payload["files"]}
    assert by_path["src/a.py"]["pagerank_pct"] == 50.0
    assert by_path["src/a.py"]["in_degree"] == 1
    assert by_path["src/b.py"]["out_degree"] == 1


@pytest.mark.asyncio
async def test_detail_filters_excluded_co_change_partners(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    """The file detail's `co_change_partners` drops the `.gitignore`d file.

    Same repro as the index test: `src/a.py` co-changed with `gen/out.py`
    (excluded) and `src/b.py` (kept). Only the kept partner is served.
    """
    repo = await create_test_repo(client, tmp_path)
    (tmp_path / "test-repo" / ".gitignore").write_text("gen/\n", encoding="utf-8")
    await _insert_nodes(
        app.state.session_factory,
        repo["id"],
        [{"node_id": "src/a.py", "pagerank": 0.5}],
    )
    await _insert_git_metadata(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/files/src/a.py")
    assert resp.status_code == 200
    partners = resp.json()["git"]["co_change_partners"]

    assert [p["file_path"] for p in partners] == ["src/b.py"]
