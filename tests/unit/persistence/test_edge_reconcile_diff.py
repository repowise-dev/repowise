"""``reconcile_edges_for_files`` writes only the rows that differ.

The table must end up exactly as the plain delete-owned-then-insert-fresh
reconcile leaves it, on both the chunked-probe and the single-scan read path.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import and_, delete, insert, or_, select

from repowise.core.ingestion.models import REGISTRATION_HINT, TARGET_OWNED_HINTS
from repowise.core.persistence import batch_upsert_graph_edges
from repowise.core.persistence.crud import _graph_edge_sync, reconcile_edges_for_files
from repowise.core.persistence.crud import graph as crud_graph
from repowise.core.persistence.models import GraphEdge, GraphNode
from tests.unit.persistence.helpers import insert_repo

_NODES = [
    ("a.py", "file", None),
    ("b.py", "file", None),
    ("c.py", "file", None),
    ("a.py::f", "symbol", "a.py"),
    ("a.py::gone", "symbol", "a.py"),  # deleted by the change; its row lingers
    ("b.py::g", "symbol", "b.py"),
    ("c.py::reg", "symbol", "c.py"),
]


def _edge(src, tgt, edge_type="imports", **extra) -> dict:
    return {"source_node_id": src, "target_node_id": tgt, "edge_type": edge_type, **extra}


_STORED = [
    _edge("a.py", "b.py"),  # unchanged
    _edge("a.py::f", "b.py::g", "calls", confidence=0.9, call_lines_json="[3]"),
    _edge("a.py::gone", "c.py", "calls"),  # owner symbol deleted
    _edge("a.py", "c.py"),  # import dropped
    _edge("c.py", "b.py"),  # not owned
    _edge("b.py", "a.py", imported_names_json=json.dumps(["x"])),  # into a.py, not owned
    _edge("c.py::reg", "a.py::f", "references", hint_source=REGISTRATION_HINT, confidence=0.8),
]

_FRESH = [
    # None takes the column default, so this still matches the stored row.
    _edge("a.py", "b.py", confidence=None),
    _edge("a.py", "b.py", "dynamic_imports", type_only=True, deferred=True),  # new
    _edge("a.py::f", "b.py::g", "calls", confidence=0.9, call_lines_json="[3, 7]"),
    _edge("a.py", "external:os", resolution_origin="import"),  # new
    _edge("c.py::reg", "a.py::f", "references", hint_source=REGISTRATION_HINT, confidence=1.0),
]

_CONTENT = (
    GraphEdge.source_node_id,
    GraphEdge.target_node_id,
    GraphEdge.edge_type,
    GraphEdge.imported_names_json,
    GraphEdge.confidence,
    GraphEdge.hint_source,
    GraphEdge.resolution_origin,
    GraphEdge.call_lines_json,
    GraphEdge.type_only,
    GraphEdge.deferred,
)


async def _seed(session, name: str) -> str:
    repo = await insert_repo(session, name=name, local_path=f"/tmp/{name}")
    for node_id, node_type, file_path in _NODES:
        session.add(
            GraphNode(
                repository_id=repo.id, node_id=node_id, node_type=node_type, file_path=file_path
            )
        )
    await batch_upsert_graph_edges(session, repo.id, _STORED)
    await session.commit()
    return repo.id


async def _rows(session, repo_id: str) -> list[tuple]:
    rows = await session.execute(select(*_CONTENT).where(GraphEdge.repository_id == repo_id))
    return sorted(tuple(r) for r in rows)


async def _legacy_reconcile(session, repo_id: str, paths: list[str], edges: list[dict]) -> None:
    """Delete every row the files own, then insert the fresh set."""
    owned = set(
        (
            await session.execute(
                select(GraphNode.node_id).where(
                    GraphNode.repository_id == repo_id,
                    or_(GraphNode.node_id.in_(paths), GraphNode.file_path.in_(paths)),
                )
            )
        ).scalars()
    ) | {e["source_node_id"] for e in edges}
    await session.execute(
        delete(GraphEdge).where(
            GraphEdge.repository_id == repo_id,
            or_(
                GraphEdge.source_node_id.in_(owned),
                and_(
                    GraphEdge.hint_source.in_(TARGET_OWNED_HINTS),
                    GraphEdge.target_node_id.in_(owned),
                ),
            ),
        )
    )
    await session.execute(insert(GraphEdge), [crud_graph._edge_values(repo_id, e) for e in edges])


@pytest.mark.parametrize("read_path", ["probe", "scan"])
async def test_diff_reconcile_matches_delete_then_insert(async_session, monkeypatch, read_path):
    # Chunk size 1 spreads the probe across chunks; a 0 threshold forces the scan.
    monkeypatch.setattr(crud_graph, "_EDGE_RECONCILE_CHUNK", 1)
    ratio = 0.0 if read_path == "scan" else float("inf")
    monkeypatch.setattr(_graph_edge_sync, "_SCAN_IDS_PER_ROW", ratio)

    expected_repo = await _seed(async_session, "legacy")
    actual_repo = await _seed(async_session, "diffed")
    await _legacy_reconcile(async_session, expected_repo, ["a.py"], _FRESH)
    await reconcile_edges_for_files(async_session, actual_repo, ["a.py"], _FRESH)
    await async_session.commit()

    expected = await _rows(async_session, expected_repo)
    assert await _rows(async_session, actual_repo) == expected
    # Sanity: the fixture really exercised drop, add, change and keep.
    keys = {r[:3] for r in expected}
    assert ("a.py", "c.py", "imports") not in keys
    assert ("a.py::gone", "c.py", "calls") not in keys
    assert ("a.py", "external:os", "imports") in keys
    assert ("b.py", "a.py", "imports") in keys


async def test_unchanged_rows_are_not_rewritten(async_session):
    repo_id = await _seed(async_session, "keep")
    ids_before = {
        (r.source_node_id, r.target_node_id, r.edge_type): r.id
        for r in (await async_session.execute(select(GraphEdge))).scalars()
    }

    removed = await reconcile_edges_for_files(async_session, repo_id, ["a.py"], _FRESH)
    await async_session.commit()

    ids_after = {
        (r.source_node_id, r.target_node_id, r.edge_type): r.id
        for r in (await async_session.execute(select(GraphEdge))).scalars()
    }
    assert ids_after[("a.py", "b.py", "imports")] == ids_before[("a.py", "b.py", "imports")]
    changed = ("a.py::f", "b.py::g", "calls")
    assert ids_after[changed] != ids_before[changed]
    # Two dropped rows plus two rewritten (the call and the registration edge).
    assert removed == 4
