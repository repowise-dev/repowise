"""External import nodes must be typed ``external`` on write (Refs #964).

Third-party import nodes (id prefix ``external:``) are created by the graph
builder without a ``node_type`` attribute. The writer defaults a missing
``node_type`` to ``'file'``, so those dependency rows were counted as repo
files by every ``WHERE node_type = 'file'`` dashboard query (~3% inflation on
real indexes). These tests pin the fix at the source: whatever the graph says,
an ``external:``-prefixed node is persisted as ``external`` — and, as a bonus,
that correct typing keeps such nodes alive through the full-persist prune.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.persistence.crud import get_graph_node
from repowise.core.pipeline.persist import _prune_stale_file_rows, persist_graph_nodes
from tests.unit.persistence.helpers import insert_repo


def _build_graph_with_external(tmp_path: Path) -> GraphBuilder:
    """Parse a tiny repo and inject a bare ``external:`` node the way the real
    builder leaves them: with no ``node_type`` attribute ever recorded."""
    traverser = FileTraverser(tmp_path)
    parser = ASTParser()
    gb = GraphBuilder(tmp_path)
    for fi in traverser.traverse():
        gb.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    gb.build()
    gb.graph().add_node("external:node:path", language="python", symbol_count=0, has_error=False)
    return gb


async def test_external_node_without_type_is_persisted_as_external(async_session, tmp_path):
    """An ``external:`` node with no recorded type is persisted as ``external``,
    never the ``'file'`` default. Verifies the write-time override end to end."""
    (tmp_path / "main.py").write_text("import pathlib\n")
    gb = _build_graph_with_external(tmp_path)

    repo = await insert_repo(async_session)
    await persist_graph_nodes(async_session, repo.id, gb)
    await async_session.commit()

    node = await get_graph_node(async_session, repo.id, "external:node:path")
    assert node is not None
    assert node.node_type == "external"


async def test_external_node_survives_full_persist_prune(async_session, tmp_path):
    """Prune never deletes an ``external:`` node, whose id matches no file on disk.

    Regression: before the fix the node was persisted as ``'file'``, so
    ``_prune_stale_file_rows`` saw it as a stale file (no real file at
    ``external:node:path``) and removed it on every full index. Typed
    ``external`` with no ``file_path``, it is no longer treated as stale.
    """
    (tmp_path / "main.py").write_text("import pathlib\n")
    gb = _build_graph_with_external(tmp_path)

    repo = await insert_repo(async_session)
    await persist_graph_nodes(async_session, repo.id, gb)
    await async_session.commit()

    # A fresh full run sees only the real file on disk; the external node is not
    # among them. It must be left alone, not pruned as a vanished file.
    await _prune_stale_file_rows(
        async_session,
        repo.id,
        current_graph_file_paths={"main.py"},
        current_git_file_paths={"main.py"},
    )
    await async_session.commit()

    node = await get_graph_node(async_session, repo.id, "external:node:path")
    assert node is not None
    assert node.node_type == "external"