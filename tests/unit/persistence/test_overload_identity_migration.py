"""An update re-keys an overload set that an older build stored under one id.

Before overloads had their own ids, ``Validate.notNull(Object)`` and
``Validate.notNull(Object, String)`` were one graph node and one
``wiki_symbols`` row, ``Validate.java::Validate::notNull``. The new build parses
them as ``notNull#1`` and ``notNull#2``. A file git did not touch must still
lose the old row, because the change is in the parser, and the first update
under the new build is the one that does it: symbols, graph nodes and edges are
reconciled repo-wide once, then the stamp returns updates to the changed set.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.parse_cache import parser_fingerprint
from repowise.core.persistence import batch_upsert_graph_edges
from repowise.core.persistence.models import GraphEdge, GraphNode, Repository, WikiSymbol
from repowise.core.pipeline.persist import (
    persist_graph_nodes,
    persist_incremental_edges,
    persist_incremental_symbols,
    stamp_edges_parser_fingerprint,
)
from tests.unit.persistence.helpers import insert_repo

_VALIDATE = """package app;

public final class Validate {
    public static void notNull(Object obj) {
        notNull(obj, "null");
    }

    public static void notNull(Object obj, String msg) {
        if (obj == null) throw new IllegalArgumentException(msg);
    }
}
"""

_CALLER = """package app;

public class Caller {
    public void run(Object o) {
        Validate.notNull(o);
    }
}
"""

_OLD_ID = "app/Validate.java::Validate::notNull"


def _build(repo_dir: Path) -> tuple[GraphBuilder, list]:
    parser = ASTParser()
    gb = GraphBuilder(repo_dir)
    parsed = []
    for fi in FileTraverser(repo_dir).traverse():
        pf = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        parsed.append(pf)
        gb.add_file(pf)
    gb.build()
    return gb, parsed


async def _seed_old_build(session, repo_id: str) -> None:
    """What the older build left: one node, one row and one edge for the set."""
    session.add(
        GraphNode(
            repository_id=repo_id,
            node_id=_OLD_ID,
            node_type="symbol",
            kind="method",
            name="notNull",
            file_path="app/Validate.java",
            start_line=8,
        )
    )
    session.add(
        WikiSymbol(
            repository_id=repo_id,
            file_path="app/Validate.java",
            symbol_id=_OLD_ID,
            name="notNull",
            qualified_name="app.Validate.Validate.notNull",
            kind="method",
            start_line=8,
            end_line=10,
        )
    )
    await batch_upsert_graph_edges(
        session,
        repo_id,
        [
            {
                "source_node_id": "app/Caller.java::Caller::run",
                "target_node_id": _OLD_ID,
                "imported_names_json": "[]",
                "edge_type": "calls",
                "confidence": 0.9,
            }
        ],
    )
    await stamp_edges_parser_fingerprint(session, repo_id, "older-parser-build")


async def _update(session, repo_id: str, gb: GraphBuilder, parsed: list, changed: list[str]) -> None:
    """The persist order both update paths use."""
    await persist_graph_nodes(session, repo_id, gb)
    await persist_incremental_symbols(session, repo_id, parsed, changed)
    await persist_incremental_edges(session, repo_id, gb, parsed, changed)
    await session.commit()


async def _ids(session, repo_id: str, column, model) -> set[str]:
    rows = await session.execute(select(column).where(model.repository_id == repo_id))
    return set(rows.scalars())


async def test_first_update_rekeys_an_untouched_file_once(async_session, tmp_path) -> None:
    repo = await insert_repo(async_session)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "Validate.java").write_text(_VALIDATE)
    (tmp_path / "app" / "Caller.java").write_text(_CALLER)
    await _seed_old_build(async_session, repo.id)
    await async_session.commit()

    gb, parsed = _build(tmp_path)
    # Only Caller.java changed; Validate.java is where the ids moved.
    await _update(async_session, repo.id, gb, parsed, ["app/Caller.java"])

    members = {f"{_OLD_ID}#1", f"{_OLD_ID}#2"}
    nodes = await _ids(async_session, repo.id, GraphNode.node_id, GraphNode)
    symbols = await _ids(async_session, repo.id, WikiSymbol.symbol_id, WikiSymbol)
    assert _OLD_ID not in nodes and members <= nodes
    assert _OLD_ID not in symbols and members <= symbols

    edges = await async_session.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.repository_id == repo.id, GraphEdge.edge_type == "calls"
        )
    )
    pairs = set(edges.all())
    assert ("app/Caller.java::Caller::run", f"{_OLD_ID}#1") in pairs
    assert (f"{_OLD_ID}#1", f"{_OLD_ID}#2") in pairs
    assert not [pair for pair in pairs if _OLD_ID in pair]

    stamp = await async_session.execute(
        select(Repository.graph_edges_parser_fingerprint).where(Repository.id == repo.id)
    )
    assert stamp.scalar_one() == parser_fingerprint()

    # Stamped: the next update is scoped to its changed set again, so a row an
    # untouched file holds is left exactly as it is.
    async_session.add(
        GraphNode(repository_id=repo.id, node_id="app/Validate.java::Validate::stray",
                  node_type="symbol", file_path="app/Validate.java")
    )
    await async_session.commit()
    await _update(async_session, repo.id, gb, parsed, ["app/Caller.java"])
    nodes = await _ids(async_session, repo.id, GraphNode.node_id, GraphNode)
    assert "app/Validate.java::Validate::stray" in nodes
