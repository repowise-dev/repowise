"""One cached code graph behind the REST and MCP path and neighbourhood views.

The REST ``/path`` endpoint built its own graph with no exclude rules, so an
excluded file could be a hop there while ``get_dependency_path`` never routed
through it. Both now read ``load_code_graph``, kept per index state.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import event, update

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
from repowise.server.services import graph_views
from tests.unit.server.conftest import create_test_repo

_A = "src/a.py"
_B = "src/b.py"  # the short route's middle hop, excluded below
_C = "src/c.py"
_D = "src/d.py"
_E = "src/e.py"


async def _seed(session_factory, repo_id: str) -> None:
    """a -> b -> c (short) and a -> d -> e -> c (long); a is the entry point."""
    async with get_session(session_factory) as session:
        for node_id in (_A, _B, _C, _D, _E):
            session.add(
                GraphNode(
                    repository_id=repo_id,
                    node_id=node_id,
                    node_type="file",
                    language="python",
                    pagerank=0.1,
                    is_entry_point=node_id == _A,
                )
            )
        for src, tgt in ((_A, _B), (_B, _C), (_A, _D), (_D, _E), (_E, _C)):
            session.add(
                GraphEdge(
                    repository_id=repo_id,
                    source_node_id=src,
                    target_node_id=tgt,
                    imported_names_json="[]",
                    edge_type="imports",
                )
            )


async def _repo(client: AsyncClient, app, tmp_path: Path, exclude: str | None) -> dict:
    repo = await create_test_repo(client, tmp_path)
    if exclude is not None:
        rw = tmp_path / "test-repo" / ".repowise"
        rw.mkdir()
        (rw / "config.yaml").write_text(f"exclude_patterns:\n  - {exclude}\n", encoding="utf-8")
    await _seed(app.state.session_factory, repo["id"])
    return repo


def _count_builds(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = graph_views._build_code_graph

    async def counting(session, repo_id, exclude_spec):
        calls.append(repo_id)
        return await real(session, repo_id, exclude_spec)

    monkeypatch.setattr(graph_views, "_build_code_graph", counting)
    return calls


@pytest.mark.asyncio
async def test_rest_path_never_routes_through_an_excluded_file(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    repo = await _repo(client, app, tmp_path, exclude=_B)

    resp = await client.get(f"/api/graph/{repo['id']}/path", params={"from": _A, "to": _C})

    assert resp.status_code == 200
    assert resp.json()["path"] == [_A, _D, _E, _C]


@pytest.mark.asyncio
async def test_rest_path_without_exclude_rules_takes_the_short_route(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    repo = await _repo(client, app, tmp_path, exclude=None)

    resp = await client.get(f"/api/graph/{repo['id']}/path", params={"from": _A, "to": _C})

    assert resp.json()["path"] == [_A, _B, _C]


@pytest.mark.asyncio
async def test_an_excluded_file_is_not_an_ego_neighbour(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    repo = await _repo(client, app, tmp_path, exclude=_B)

    resp = await client.get(f"/api/graph/{repo['id']}/ego", params={"node_id": _A, "hops": 1})

    assert resp.status_code == 200
    body = resp.json()
    assert {n["node_id"] for n in body["nodes"]} == {_A, _D}
    assert body["outbound_count"] == 1


@pytest.mark.asyncio
async def test_an_excluded_ego_centre_is_not_found(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    repo = await _repo(client, app, tmp_path, exclude=_B)

    resp = await client.get(f"/api/graph/{repo['id']}/ego", params={"node_id": _B})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_entry_point_reach_skips_an_excluded_file(
    client: AsyncClient, app, tmp_path: Path
) -> None:
    repo = await _repo(client, app, tmp_path, exclude=_B)

    resp = await client.get(f"/api/graph/{repo['id']}/entry-points")

    assert resp.status_code == 200
    body = resp.json()
    assert {n["node_id"] for n in body["nodes"]} == {_A, _C, _D, _E}
    assert all(_B not in (link["source"], link["target"]) for link in body["links"])

@pytest.mark.asyncio
async def test_repeat_requests_at_one_index_state_load_the_graph_once(
    client: AsyncClient, app, tmp_path: Path, monkeypatch
) -> None:
    repo = await _repo(client, app, tmp_path, exclude=None)
    builds = _count_builds(monkeypatch)
    url = f"/api/graph/{repo['id']}/path"

    await client.get(url, params={"from": _A, "to": _C})
    # /ego reads the same graph, so it is a hit too.
    await client.get(f"/api/graph/{repo['id']}/ego", params={"node_id": _A})

    edge_reads: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        if "FROM graph_edges" in statement:
            edge_reads.append(statement)

    engine = app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        resp = await client.get(url, params={"from": _A, "to": _C})
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert resp.json()["path"] == [_A, _B, _C]
    assert builds == [repo["id"]]
    assert edge_reads == [], "a cached graph read the edge table again"


@pytest.mark.asyncio
async def test_a_new_index_state_reloads_the_graph(
    client: AsyncClient, app, tmp_path: Path, monkeypatch
) -> None:
    repo = await _repo(client, app, tmp_path, exclude=None)
    builds = _count_builds(monkeypatch)
    url = f"/api/graph/{repo['id']}/path"

    assert (await client.get(url, params={"from": _A, "to": _C})).json()["path"] == [_A, _B, _C]

    # An index run drops the b -> c edge and moves the stamp.
    async with get_session(app.state.session_factory) as session:
        await session.execute(
            GraphEdge.__table__.delete().where(
                GraphEdge.repository_id == repo["id"],
                GraphEdge.source_node_id == _B,
            )
        )
        await session.execute(
            update(Repository).where(Repository.id == repo["id"]).values(head_commit="f" * 40)
        )

    resp = await client.get(url, params={"from": _A, "to": _C})

    assert resp.json()["path"] == [_A, _D, _E, _C]
    assert len(builds) == 2

