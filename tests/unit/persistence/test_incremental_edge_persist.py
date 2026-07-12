"""Incremental updates must refresh ``graph_edges`` for changed files.

The incremental update path rebuilds the in-memory graph but historically only
the full-init path persisted edges, so ``graph_edges`` froze at the last full
index: a new import stayed invisible and an import a file dropped lingered as a
false adjacency (the Phase E flow-path traversal reads adjacency straight from
this table). These tests drive the real parser + graph builder over a file whose
imports change and assert the persisted edges follow — the added edge appears,
the dropped edge is pruned, and edges owned by unchanged files are left alone.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.persistence import batch_upsert_graph_edges
from repowise.core.persistence.models import GraphEdge
from repowise.core.pipeline.persist import persist_graph_nodes, persist_incremental_edges
from tests.unit.persistence.helpers import insert_repo


def _build_graph(repo_dir: Path) -> tuple[GraphBuilder, list]:
    """Parse every file under *repo_dir* and build the ingestion graph."""
    traverser = FileTraverser(repo_dir)
    parser = ASTParser()
    gb = GraphBuilder(repo_dir)
    parsed = []
    for fi in traverser.traverse():
        pf = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        parsed.append(pf)
        gb.add_file(pf)
    gb.build()
    return gb, parsed


def _graph_edges(gb: GraphBuilder) -> list[dict]:
    return [
        {
            "source_node_id": u,
            "target_node_id": v,
            "imported_names_json": json.dumps(d.get("imported_names", [])),
            "edge_type": d.get("edge_type", "imports"),
            "confidence": d.get("confidence", 1.0),
        }
        for u, v, d in gb.graph().edges(data=True)
    ]


async def _db_edge_set(session, repo_id: str) -> set[tuple[str, str, str]]:
    rows = (
        (await session.execute(select(GraphEdge).where(GraphEdge.repository_id == repo_id)))
        .scalars()
        .all()
    )
    return {(r.source_node_id, r.target_node_id, r.edge_type) for r in rows}


async def _seed_full(session, repo_id: str, gb: GraphBuilder) -> None:
    """Mirror the full-init persist: nodes first, then the whole edge set."""
    await persist_graph_nodes(session, repo_id, gb)
    await batch_upsert_graph_edges(session, repo_id, _graph_edges(gb))


async def test_incremental_update_reconciles_changed_file_edges(async_session, tmp_path):
    repo = await insert_repo(async_session)

    # v1: b.py imports a.py; d.py imports b.py. c.py stands ready as b's next
    # import target.
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "c.py").write_text("y = 2\n")
    (tmp_path / "b.py").write_text("from a import x\n")
    (tmp_path / "d.py").write_text("import b\n")

    gb1, _ = _build_graph(tmp_path)
    await _seed_full(async_session, repo.id, gb1)
    await async_session.commit()

    before = await _db_edge_set(async_session, repo.id)
    # Sanity: the fixture really produced the edges the test reasons about.
    assert ("b.py", "a.py", "imports") in before
    assert ("d.py", "b.py", "imports") in before

    # v2: b.py drops its import of a.py and imports c.py instead.
    (tmp_path / "b.py").write_text("from c import y\n")
    gb2, parsed2 = _build_graph(tmp_path)

    # Nodes are refreshed before edges on the real path; the reconcile reads
    # graph_nodes to scope the delete, so do the same here.
    await persist_graph_nodes(async_session, repo.id, gb2)
    await persist_incremental_edges(async_session, repo.id, gb2, parsed2, ["b.py"])
    await async_session.commit()

    after = await _db_edge_set(async_session, repo.id)
    # The dropped import is gone; the new import is persisted.
    assert ("b.py", "a.py", "imports") not in after
    assert ("b.py", "c.py", "imports") in after
    # An edge owned by an unchanged file (d.py -> b.py) is left untouched.
    assert ("d.py", "b.py", "imports") in after


async def test_incremental_edge_persist_leaves_unchanged_files_untouched(async_session, tmp_path):
    """Only edges owned by the changed set are rewritten; others keep their rows."""
    repo = await insert_repo(async_session)

    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("from a import x\n")
    (tmp_path / "c.py").write_text("from a import x\n")

    gb1, _ = _build_graph(tmp_path)
    await _seed_full(async_session, repo.id, gb1)
    await async_session.commit()
    before = await _db_edge_set(async_session, repo.id)
    assert ("b.py", "a.py", "imports") in before
    assert ("c.py", "a.py", "imports") in before

    # b.py changes but keeps importing a.py; c.py is not in the changed set.
    (tmp_path / "b.py").write_text("from a import x\ny = x + 1\n")
    gb2, parsed2 = _build_graph(tmp_path)
    await persist_graph_nodes(async_session, repo.id, gb2)
    await persist_incremental_edges(async_session, repo.id, gb2, parsed2, ["b.py"])
    await async_session.commit()

    after = await _db_edge_set(async_session, repo.id)
    # Both survive: b.py re-inserted its (unchanged) edge, c.py was never touched.
    assert ("b.py", "a.py", "imports") in after
    assert ("c.py", "a.py", "imports") in after
