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
from types import SimpleNamespace

from sqlalchemy import select

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.parse_cache import parser_fingerprint
from repowise.core.persistence import batch_upsert_graph_edges
from repowise.core.persistence.models import GraphEdge, Repository
from repowise.core.pipeline.persist import (
    persist_graph_nodes,
    persist_incremental_edges,
    persist_ingestion,
    stamp_edges_parser_fingerprint,
)
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


async def _seed_full(
    session, repo_id: str, gb: GraphBuilder, *, fingerprint: str | None = None
) -> None:
    """Mirror the full-init persist: nodes first, then the whole edge set.

    *fingerprint* is what the build that wrote those edges stamped. The real
    full path never stamps (see ``stamp_edges_parser_fingerprint``); this seeds
    the stamp directly so a test can pick the store state it wants.
    """
    await persist_graph_nodes(session, repo_id, gb)
    await batch_upsert_graph_edges(session, repo_id, _graph_edges(gb))
    await stamp_edges_parser_fingerprint(session, repo_id, fingerprint or parser_fingerprint())


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


async def test_full_and_incremental_persist_retain_call_site_lines(async_session, tmp_path):
    """Both graph write paths preserve the exact sites behind a collapsed call edge."""
    repo = await insert_repo(async_session)
    source = "def load():\n    pass\n\ndef run():\n    load()\n"
    (tmp_path / "calls.py").write_text(source)
    gb1, parsed1 = _build_graph(tmp_path)

    await persist_ingestion(
        SimpleNamespace(
            parsed_files=parsed1,
            graph_builder=gb1,
            external_systems=[],
            execution_flow_report=None,
            source_map={},
        ),
        async_session,
        repo.id,
    )
    await async_session.commit()

    call_edge = (
        await async_session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repo.id,
                GraphEdge.edge_type == "calls",
            )
        )
    ).scalar_one()
    assert json.loads(call_edge.call_lines_json) == [5]

    (tmp_path / "calls.py").write_text(source + "    load()\n")
    gb2, parsed2 = _build_graph(tmp_path)
    await persist_graph_nodes(async_session, repo.id, gb2)
    await persist_incremental_edges(async_session, repo.id, gb2, parsed2, ["calls.py"])
    await async_session.commit()

    call_edge = (
        await async_session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repo.id,
                GraphEdge.edge_type == "calls",
            )
        )
    ).scalar_one()
    assert json.loads(call_edge.call_lines_json) == [5, 6]


async def _stored_fingerprint(session, repo_id: str) -> str | None:
    return (
        await session.execute(
            select(Repository.graph_edges_parser_fingerprint).where(Repository.id == repo_id)
        )
    ).scalar_one_or_none()


async def _seed_missing_edge(session, tmp_path: Path, fingerprint: str | None):
    """A store whose *unchanged* c.py is missing an edge the current parser finds.

    Stands in for what a parser fix does to a shipped store: the in-memory graph
    gains edges on every file, but the table only has the ones the old build
    extracted. *fingerprint* is what that old build stamped (``None`` for a
    store predating the stamp entirely).
    """
    repo = await insert_repo(session)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("from a import x\n")
    (tmp_path / "c.py").write_text("from a import x\n")

    gb, parsed = _build_graph(tmp_path)
    await _seed_full(session, repo.id, gb, fingerprint=fingerprint or parser_fingerprint())
    if fingerprint is None:
        await session.execute(
            Repository.__table__.update()
            .where(Repository.id == repo.id)
            .values(graph_edges_parser_fingerprint=None)
        )
    # Drop the edge the old build failed to extract, leaving c.py stale.
    await session.execute(
        GraphEdge.__table__.delete().where(
            GraphEdge.repository_id == repo.id, GraphEdge.source_node_id == "c.py"
        )
    )
    await session.commit()
    assert ("c.py", "a.py", "imports") not in await _db_edge_set(session, repo.id)
    return repo, gb, parsed


async def test_parser_change_widens_reconcile_to_unchanged_files(async_session, tmp_path):
    """A parser fix must reach files git considers unchanged, without a reindex.

    Scoping the write to the changed set is right for content changes and wrong
    for extraction changes: the latter alter every file's edges at once. The
    stored parser fingerprint is what tells the two apart.
    """
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, "older-parser-build")

    # Only b.py changed on disk, yet the run is under a different parser build.
    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)
    # Stamped, so the next update goes back to the cheap changed-set scoping.
    assert await _stored_fingerprint(async_session, repo.id) == parser_fingerprint()


async def test_store_predating_the_stamp_is_backfilled_once(async_session, tmp_path):
    """A NULL stamp is a mismatch: every already-shipped store takes the widen."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, None)

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)
    assert await _stored_fingerprint(async_session, repo.id) == parser_fingerprint()


async def test_matching_fingerprint_does_not_widen(async_session, tmp_path):
    """Same parser build: stay scoped to the changed set, so the widen is one-shot."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, parser_fingerprint())

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    # c.py was not in the changed set and the parser did not change, so its
    # rows are left exactly as they were.
    assert ("c.py", "a.py", "imports") not in await _db_edge_set(async_session, repo.id)


async def test_full_reindex_does_not_suppress_the_widen(async_session, tmp_path):
    """A re-index must not mark a store parser-current — it cannot clear surplus edges.

    ``persist_ingestion`` upserts edges and prunes only vanished files' nodes,
    so re-indexing an old store adds the new build's edges while leaving the old
    build's wrong ones behind. If it stamped the fingerprint, the widen — the
    only path that deletes those — would never run again for that store.
    """
    repo = await insert_repo(async_session)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("from a import x\n")
    (tmp_path / "c.py").write_text("x = 3\n")  # imports nothing

    gb, parsed = _build_graph(tmp_path)
    # An older build resolved a c.py -> a.py import the current parser does not.
    await _seed_full(async_session, repo.id, gb, fingerprint="older-parser-build")
    await batch_upsert_graph_edges(
        async_session,
        repo.id,
        [
            {
                "source_node_id": "c.py",
                "target_node_id": "a.py",
                "imported_names_json": "[]",
                "edge_type": "imports",
                "confidence": 1.0,
            }
        ],
    )
    await async_session.commit()
    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)

    # A full re-index on the new build: every edge rewritten, none deleted.
    await persist_ingestion(
        SimpleNamespace(
            parsed_files=parsed,
            graph_builder=gb,
            external_systems=[],
            execution_flow_report=None,
            source_map={},
        ),
        async_session,
        repo.id,
    )
    await async_session.commit()
    # The surplus edge survived the re-index, and the store is still unstamped.
    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)
    assert await _stored_fingerprint(async_session, repo.id) == "older-parser-build"

    # So the next update still widens, and that is what clears it.
    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()
    assert ("c.py", "a.py", "imports") not in await _db_edge_set(async_session, repo.id)
