"""HTTP graph views hide stale rows for files the repo rules exclude.

Files stay indexed after a path is gitignored, so the rows below are written
first and ``archive/`` is ignored afterwards, as a user would. Every view must
then serve no ``archive/`` path, and no count may include an edge with an end
there, whichever side it is on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DeadCodeFinding, GraphEdge, GraphNode
from tests.unit.server.conftest import create_test_repo

_MAIN = "src/main.py"
_UTIL = "src/util.py"
_UNUSED = "src/unused.py"
_OTHER = "src/other.py"
_OLD = "archive/old.py"
_USER = "archive/user.py"

_HELPER = f"{_UTIL}::helper"
_RUN = f"{_MAIN}::run"
_STALE_CALLER = f"{_USER}::caller"

# file -> (community, pagerank)
_FILES = {
    _MAIN: (1, 0.5),
    _UTIL: (1, 0.4),
    _UNUSED: (2, 0.05),
    _OTHER: (2, 0.1),
    _OLD: (2, 0.9),  # highest PageRank: it would win any cap it could enter
    _USER: (2, 0.8),
}

_EDGES = [
    (_MAIN, _UTIL, "imports"),
    (_MAIN, _OLD, "imports"),  # export link, module edge, hot-file neighbour
    (_USER, _UTIL, "imports"),  # file dependent and in-degree
    (_UNUSED, _OLD, "imports"),  # dead seed -> excluded neighbour
    (_OLD, _UNUSED, "imports"),  # excluded neighbour -> dead seed
    (_UTIL, _OTHER, "imports"),  # the one allowed cross-community edge
    (_UNUSED, _MAIN, "co_changes"),  # history, not a neighbour
    (_RUN, _HELPER, "calls"),
    (_STALE_CALLER, _HELPER, "calls"),
]


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        for path, (community, pagerank) in _FILES.items():
            session.add(
                GraphNode(
                    repository_id=repo_id,
                    node_id=path,
                    node_type="file",
                    language="python",
                    file_path=path,
                    pagerank=pagerank,
                    community_id=community,
                    is_entry_point=path == _MAIN,
                )
            )
        for symbol_id in (_HELPER, _RUN, _STALE_CALLER):
            path, name = symbol_id.split("::")
            session.add(
                GraphNode(
                    repository_id=repo_id,
                    node_id=symbol_id,
                    node_type="symbol",
                    language="python",
                    file_path=path,
                    name=name,
                    kind="function",
                    pagerank=0.01,
                    community_id=_FILES[path][0],
                    # The stale caller out-scores the real entry point.
                    community_meta_json=json.dumps(
                        {"entry_point_score": {_RUN: 0.5, _STALE_CALLER: 0.9}.get(symbol_id, 0.0)}
                    ),
                )
            )
        for src, dst, edge_type in _EDGES:
            session.add(
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id=src,
                    target_node_id=dst,
                    imported_names_json="[]",
                    edge_type=edge_type,
                    confidence=1.0,
                )
            )
        for path in (_UNUSED, _OLD):
            session.add(
                DeadCodeFinding(
                    repository_id=repo_id,
                    file_path=path,
                    kind="unreachable_file",
                    status="open",
                    confidence=0.95,
                )
            )
        for path, commits in ((_OLD, 50), (_MAIN, 10)):
            await crud.upsert_git_metadata(
                session,
                repository_id=repo_id,
                file_path=path,
                commit_count_30d=commits,
                commit_count_90d=commits,
            )
        await session.flush()


@pytest.fixture
async def repo_id(client: AsyncClient, app, tmp_path: Path) -> str:
    repo = await create_test_repo(client, tmp_path)
    await _seed(app.state.session_factory, repo["id"])
    # Ignored after indexing: the rows above stay in the store.
    (tmp_path / "test-repo" / ".gitignore").write_text("archive/\n", encoding="utf-8")
    return repo["id"]


def _no_archive(payload: object) -> None:
    assert "archive/" not in str(payload), payload


@pytest.mark.asyncio
async def test_export_drops_excluded_nodes_and_links_before_the_cap(
    client: AsyncClient, repo_id: str
) -> None:
    body = (await client.get(f"/api/graph/{repo_id}")).json()
    _no_archive(body)
    assert body["total_node_count"] == 4
    assert {(link["source"], link["target"]) for link in body["links"]} >= {(_MAIN, _UTIL)}

    # The excluded files out-rank everything; a cap of two still serves allowed files.
    capped = (await client.get(f"/api/graph/{repo_id}", params={"limit": 2})).json()
    _no_archive(capped)
    assert len(capped["nodes"]) == 2


@pytest.mark.asyncio
async def test_module_graph_counts_no_excluded_file_or_edge(
    client: AsyncClient, repo_id: str
) -> None:
    body = (await client.get(f"/api/graph/{repo_id}/modules")).json()
    _no_archive(body)
    assert sum(n["file_count"] for n in body["nodes"]) == 4
    assert body["edges"] == []


@pytest.mark.asyncio
async def test_dead_nodes_take_no_excluded_seed_or_neighbour(
    client: AsyncClient, repo_id: str
) -> None:
    body = (await client.get(f"/api/graph/{repo_id}/dead-nodes")).json()
    _no_archive(body)
    by_id = {n["node_id"]: n["confidence_group"] for n in body["nodes"]}
    # src/main.py is joined only by a co-change row, which pulls in no neighbour.
    assert by_id == {_UNUSED: "certain"}


@pytest.mark.asyncio
async def test_hot_files_cap_runs_after_exclusion(client: AsyncClient, repo_id: str) -> None:
    body = (await client.get(f"/api/graph/{repo_id}/hot-files", params={"limit": 1})).json()
    _no_archive(body)
    by_id = {n["node_id"]: n["commit_count"] for n in body["nodes"]}
    assert by_id == {_MAIN: 10, _UTIL: 0}


@pytest.mark.asyncio
async def test_file_detail_degree_and_dependents_skip_excluded_ends(
    client: AsyncClient, repo_id: str
) -> None:
    body = (await client.get(f"/api/repos/{repo_id}/files/{_UTIL}")).json()
    graph = body["graph"]
    _no_archive(graph)
    assert graph["in_degree"] == 1
    assert [d["node_id"] for d in graph["dependents"]] == [_MAIN]

    breakdown = (
        await client.get(
            f"/api/repos/{repo_id}/health/files/breakdown", params={"file_path": _MAIN}
        )
    ).json()
    assert body["health"]["signals"]["in_degree"] == 1
    # main imports util and archive/old.py; only the first is counted.
    assert breakdown["signals"]["out_degree"] == 1


@pytest.mark.asyncio
async def test_metrics_degree_skips_excluded_ends(client: AsyncClient, repo_id: str) -> None:
    file_body = (
        await client.get(f"/api/graph/{repo_id}/metrics", params={"node_id": _MAIN})
    ).json()
    assert file_body["out_degree"] == 1  # util; the import of archive/old.py is hidden

    symbol_body = (
        await client.get(f"/api/graph/{repo_id}/metrics", params={"node_id": _HELPER})
    ).json()
    assert symbol_body["in_degree"] == 1


@pytest.mark.asyncio
async def test_symbol_relations_rows_and_totals_skip_excluded_callers(
    client: AsyncClient, repo_id: str
) -> None:
    body = (
        await client.get(f"/api/graph/{repo_id}/callers-callees", params={"symbol_id": _HELPER})
    ).json()
    _no_archive(body)
    assert body["caller_count"] == 1
    assert [c["symbol_id"] for c in body["callers"]] == [_RUN]


@pytest.mark.asyncio
async def test_community_views_count_no_excluded_member_or_edge(
    client: AsyncClient, repo_id: str
) -> None:
    arch = (await client.get(f"/api/graph/{repo_id}/architecture")).json()
    _no_archive(arch)
    members = {n["community_id"]: n["member_count"] for n in arch["nodes"]}
    assert members == {1: 2, 2: 2}
    assert {(e["source"], e["target"], e["edge_count"]) for e in arch["edges"]} == {(1, 2, 1)}

    listed = (await client.get(f"/api/graph/{repo_id}/communities")).json()
    _no_archive(listed)
    assert {c["community_id"]: c["hidden_member_count"] for c in listed} == {1: 0, 2: 0}

    detail = (await client.get(f"/api/graph/{repo_id}/communities/1")).json()
    _no_archive(detail["members"])
    assert [
        (n["community_id"], n["cross_edge_count"]) for n in detail["neighboring_communities"]
    ] == [(2, 1)]

    slice_ = (await client.get(f"/api/graph/{repo_id}/communities/1/slice")).json()
    _no_archive(slice_)
    boundary = {n["node_id"] for n in slice_["nodes"] if n.get("is_boundary")}
    # The slice draws co-change ties like the canvas does; archive/ takes no stub slot.
    assert boundary == {_OTHER, _UNUSED}


@pytest.mark.asyncio
async def test_execution_flows_take_no_excluded_entry_point_or_hop(
    client: AsyncClient, repo_id: str
) -> None:
    body = (await client.get(f"/api/graph/{repo_id}/execution-flows", params={"top_n": 1})).json()
    _no_archive(body)
    assert [(f["entry_point"], f["trace"]) for f in body["flows"]] == [(_RUN, [_RUN, _HELPER])]

    resp = await client.get(
        f"/api/graph/{repo_id}/execution-flows", params={"entry_point": _STALE_CALLER}
    )
    assert resp.status_code == 404
