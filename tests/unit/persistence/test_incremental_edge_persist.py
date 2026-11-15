"""Incremental updates must refresh ``graph_edges`` for changed files.

The incremental update path rebuilds the in-memory graph but historically only
the full-init path persisted edges, so ``graph_edges`` froze at the last full
index: a new import stayed invisible and an import a file dropped lingered as a
false adjacency (the Phase E flow-path traversal reads adjacency straight from
this table). These tests drive the real parser + graph builder over a file whose
imports change and assert the persisted edges follow — the added edge appears,
the dropped edge is pruned, and edges owned by unchanged files match the graph.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.parse_cache import parser_fingerprint
from repowise.core.persistence import batch_upsert_graph_edges
from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
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
    """An unchanged file's edges that still hold keep their rows."""
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
    # Both survive: b.py re-inserted its (unchanged) edge, c.py's still holds.
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
    # Stamped with the running build.
    assert await _stored_fingerprint(async_session, repo.id) == parser_fingerprint()


async def test_store_predating_the_stamp_is_backfilled_once(async_session, tmp_path):
    """A NULL stamp is a mismatch: every already-shipped store takes the widen."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, None)

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)
    assert await _stored_fingerprint(async_session, repo.id) == parser_fingerprint()


async def test_an_unchanged_files_edge_into_a_changed_file_follows_the_graph(
    async_session, tmp_path
):
    """Same parser build: a file git did not touch still gains an edge into a changed one."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, parser_fingerprint())

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["a.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)


async def test_an_edge_that_differs_from_the_last_persisted_graph_is_reconciled(
    async_session, tmp_path
):
    """The stored snapshot names files whose edges moved without touching the change set."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, parser_fingerprint())
    graph = gb.graph()
    gb.previous_seed_edges = {
        "nodes": [(n, dict(d)) for n, d in graph.nodes(data=True)],
        "edges": [(u, v, dict(d)) for u, v, d in graph.edges(data=True) if u != "c.py"],
    }

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)


async def test_a_file_the_change_does_not_reach_is_not_reconciled(async_session, tmp_path):
    """With a snapshot that matches it, a file the change does not reach is not read or written."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, parser_fingerprint())
    graph = gb.graph()
    gb.previous_seed_edges = {
        "nodes": [(n, dict(d)) for n, d in graph.nodes(data=True)],
        "edges": [(u, v, dict(d)) for u, v, d in graph.edges(data=True)],
    }

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") not in await _db_edge_set(async_session, repo.id)


async def test_unchanged_file_keeps_co_change_rows_the_graph_does_not_cover(
    async_session, tmp_path
):
    """Without a complete history walk the graph holds only the changed files' pairs."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, parser_fingerprint())
    async_session.add(
        GraphEdge(
            repository_id=repo.id,
            source_node_id="c.py",
            target_node_id="a.py",
            edge_type="co_changes",
        )
    )
    await async_session.commit()

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "co_changes") in await _db_edge_set(async_session, repo.id)


async def test_index_into_a_fresh_store_stamps_the_parser(async_session, tmp_path):
    """``init`` stamps the build that wrote the edges when nothing preceded them.

    A fresh store has no surplus edges for a widen to clear. Left unstamped, a
    later parser-only release could not tell it from a current one on a quiet
    checkout.
    """
    repo = await insert_repo(async_session)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("from a import x\n")
    gb, parsed = _build_graph(tmp_path)

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

    assert await _stored_fingerprint(async_session, repo.id) == parser_fingerprint()


async def test_full_reindex_prunes_surplus_edges_and_stamps(async_session, tmp_path):
    """A full re-index leaves exactly the fresh graph behind, so it can stamp.

    ``persist_ingestion`` used to upsert only, so re-indexing an old store kept
    the old build's wrong edges. It now prunes them in the same transaction, and
    because nothing surplus remains the store is parser-current.
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

    assert ("c.py", "a.py", "imports") not in await _db_edge_set(async_session, repo.id)
    assert await _db_edge_set(async_session, repo.id) == {
        (u, v, d.get("edge_type", "imports")) for u, v, d in gb.graph().edges(data=True)
    }
    assert await _stored_fingerprint(async_session, repo.id) == parser_fingerprint()


async def test_full_reindex_deletes_removed_edge_and_node(async_session, tmp_path):
    """Issue #4406: re-persisting a graph minus one edge and one node drops both rows."""
    repo = await insert_repo(async_session)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("from a import x\n")
    (tmp_path / "c.py").write_text("from a import x\n")

    def _view(gb, parsed):
        return SimpleNamespace(
            parsed_files=parsed,
            graph_builder=gb,
            external_systems=[],
            execution_flow_report=None,
            source_map={},
        )

    gb1, parsed1 = _build_graph(tmp_path)
    await persist_ingestion(_view(gb1, parsed1), async_session, repo.id)
    await async_session.commit()
    assert ("b.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)
    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)

    # b.py drops its import; c.py disappears from the graph entirely.
    (tmp_path / "b.py").write_text("y = 2\n")
    (tmp_path / "c.py").unlink()
    gb2, parsed2 = _build_graph(tmp_path)
    await persist_ingestion(_view(gb2, parsed2), async_session, repo.id)
    await async_session.commit()

    edges = await _db_edge_set(async_session, repo.id)
    assert ("b.py", "a.py", "imports") not in edges
    assert ("c.py", "a.py", "imports") not in edges
    node_ids = set(
        (
            await async_session.execute(
                select(GraphNode.node_id).where(GraphNode.repository_id == repo.id)
            )
        )
        .scalars()
        .all()
    )
    assert "c.py" not in node_ids
    assert node_ids == set(gb2.graph().nodes)


async def test_full_reindex_with_empty_graph_prunes_nothing(async_session, tmp_path):
    """An empty graph means a broken run; it must not wipe the stored graph."""
    repo = await insert_repo(async_session)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("from a import x\n")
    gb, _parsed = _build_graph(tmp_path)
    await _seed_full(async_session, repo.id, gb)
    await async_session.commit()
    before = await _db_edge_set(async_session, repo.id)

    from repowise.core.pipeline.persist import prune_absent_graph_rows

    assert await prune_absent_graph_rows(async_session, repo.id, set(), []) == (0, 0)
    assert await _db_edge_set(async_session, repo.id) == before


async def test_a_registration_edge_follows_the_decorated_file(async_session, tmp_path):
    """``@rule`` lives in the decorated file, so editing only that file adds and drops the edge."""
    repo = await insert_repo(async_session)
    registry = "_RULES = []\n\n\ndef rule(fn):\n    _RULES.append(fn)\n    return fn\n"
    plain = "from reg import rule\n\n\ndef _check():\n    return 1\n"
    (tmp_path / "reg.py").write_text(registry)
    (tmp_path / "b.py").write_text(plain)
    gb1, _ = _build_graph(tmp_path)
    await _seed_full(async_session, repo.id, gb1)
    await async_session.commit()
    edge = ("reg.py::rule", "b.py::_check", "references")
    assert edge not in await _db_edge_set(async_session, repo.id)

    for text, expected in (
        (plain.replace("def _check", "@rule\ndef _check"), True),
        (plain, False),
    ):
        (tmp_path / "b.py").write_text(text)
        gb, parsed = _build_graph(tmp_path)
        await persist_graph_nodes(async_session, repo.id, gb)
        await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
        await async_session.commit()
        assert (edge in await _db_edge_set(async_session, repo.id)) is expected


async def _co_change_rows(session, repo_id: str) -> set[tuple[str, str]]:
    return {(s, t) for s, t, kind in await _db_edge_set(session, repo_id) if kind == "co_changes"}


async def test_complete_co_change_refresh_syncs_pairs_between_unchanged_files(
    async_session, tmp_path: Path
) -> None:
    """History moves pairs between files no commit touched; the stored set follows."""
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.py").write_text(f"def {name}():\n    return 1\n", encoding="utf-8")
    repo = await insert_repo(async_session, local_path=str(tmp_path))
    gb, parsed = _build_graph(tmp_path)
    gb._graph.add_edge("a.py", "b.py", edge_type="co_changes")
    await _seed_full(async_session, repo.id, gb)

    gb._graph.remove_edge("a.py", "b.py")
    gb._graph.add_edge("b.py", "c.py", edge_type="co_changes")
    await persist_incremental_edges(async_session, repo.id, gb, parsed, [])
    assert await _co_change_rows(async_session, repo.id) == {("a.py", "b.py")}

    gb.co_change_edges_complete = True
    await persist_incremental_edges(async_session, repo.id, gb, parsed, [])
    assert await _co_change_rows(async_session, repo.id) == {("b.py", "c.py")}

    # A row stored the other way round (older builds) converges to the graph's direction.
    async_session.add(
        GraphEdge(
            repository_id=repo.id,
            source_node_id="c.py",
            target_node_id="b.py",
            edge_type="co_changes",
        )
    )
    await async_session.flush()
    await persist_incremental_edges(async_session, repo.id, gb, parsed, [])
    assert await _co_change_rows(async_session, repo.id) == {("b.py", "c.py")}


async def test_unchanged_files_keep_their_edges_when_the_graph_is_short(async_session, tmp_path):
    """A failed parse or synthetic-edge pass leaves the graph missing real edges."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, parser_fingerprint())
    gb.traversed_file_paths = {pf.file_info.path for pf in parsed} | {"broken.py"}
    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])

    gb.traversed_file_paths = {pf.file_info.path for pf in parsed}
    gb.synthetic_edges_complete = False
    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") not in await _db_edge_set(async_session, repo.id)


async def test_a_short_graph_leaves_the_parser_stamp_for_the_next_update(async_session, tmp_path):
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, "older-parser-build")
    gb.traversed_file_paths = {pf.file_info.path for pf in parsed} | {"broken.py"}

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert await _stored_fingerprint(async_session, repo.id) == "older-parser-build"


def _stored_snapshot(gb, *, extra_nodes=()) -> dict[str, list]:
    """The last persisted graph: *gb* without the edge the store lacks."""
    return {
        "nodes": [*gb.graph().nodes(data=True), *extra_nodes],
        "edges": [
            (u, v, d) for u, v, d in gb.graph().edges(data=True) if (u, v) != ("c.py", "a.py")
        ],
    }


async def test_a_file_that_never_parsed_leaves_the_graph_complete(async_session, tmp_path):
    """It had no rows to lose, so the other files are reconciled and stamped as usual."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, "older-parser-build")
    gb.traversed_file_paths = {pf.file_info.path for pf in parsed} | {"broken.py"}
    gb.previous_seed_edges = _stored_snapshot(gb)

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)
    assert await _stored_fingerprint(async_session, repo.id) == parser_fingerprint()


async def test_a_file_that_stopped_parsing_still_makes_the_graph_short(async_session, tmp_path):
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, "older-parser-build")
    gb.traversed_file_paths = {pf.file_info.path for pf in parsed} | {"broken.py"}
    gb.previous_seed_edges = _stored_snapshot(
        gb, extra_nodes=[("broken.py::f", {"node_type": "function", "file_path": "broken.py"})]
    )

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") not in await _db_edge_set(async_session, repo.id)
    assert await _stored_fingerprint(async_session, repo.id) == "older-parser-build"


async def test_a_bare_file_node_that_stopped_parsing_leaves_the_graph_complete(async_session, tmp_path):
    """A file node with no symbol and no edge gives no caller anything to lose."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, "older-parser-build")
    gb.traversed_file_paths = {pf.file_info.path for pf in parsed} | {"broken.py"}
    gb.previous_seed_edges = _stored_snapshot(
        gb, extra_nodes=[("broken.py", {"node_type": "file", "language": "python"})]
    )

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)
    assert await _stored_fingerprint(async_session, repo.id) == parser_fingerprint()


async def test_rows_restored_for_a_file_that_stopped_parsing_leave_the_graph_complete(
    async_session, tmp_path
):
    """The update put the file's last rows back, so none of them reads as gone."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, "older-parser-build")
    gb.traversed_file_paths = {pf.file_info.path for pf in parsed} | {"broken.py"}
    restored = [
        ("broken.py", {"node_type": "file", "language": "python"}),
        ("broken.py::f", {"node_type": "function", "file_path": "broken.py"}),
    ]
    gb.previous_seed_edges = _stored_snapshot(gb, extra_nodes=restored)
    gb.previous_seed_edges["edges"].append(("broken.py", "broken.py::f", {"edge_type": "defines"}))
    for node_id, attrs in restored:
        gb.graph().add_node(node_id, **attrs)
    gb.graph().add_edge("broken.py", "broken.py::f", edge_type="defines")

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert await _stored_fingerprint(async_session, repo.id) == parser_fingerprint()


async def test_without_a_snapshot_every_file_is_reconciled(async_session, tmp_path):
    """The rebuilt graph alone cannot show a call that stopped resolving."""
    repo, gb, parsed = await _seed_missing_edge(async_session, tmp_path, parser_fingerprint())

    await persist_incremental_edges(async_session, repo.id, gb, parsed, ["b.py"])
    await async_session.commit()

    assert ("c.py", "a.py", "imports") in await _db_edge_set(async_session, repo.id)
