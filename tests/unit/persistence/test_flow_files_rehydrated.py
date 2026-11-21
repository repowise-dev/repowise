"""The flow code reads the same files from a live and a rehydrated graph.

Import targets carry no ``node_type`` in the live graph, and the writer stores
them as ``file`` rows. A graph read back from SQL must not count them as code,
nor read them for entry points.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.generation.flow_entries import declared_entries, line_reader
from repowise.core.generation.flow_pages import _code_file_count
from repowise.core.generation.onboarding.surface import surfaces
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.pipeline.persist import persist_graph_nodes
from repowise.core.pipeline.upgrade import rehydrate_graph_builder
from tests.unit.persistence.helpers import insert_repo


def _build(repo_dir: Path) -> GraphBuilder:
    parser = ASTParser()
    gb = GraphBuilder(repo_dir)
    for fi in FileTraverser(repo_dir).traverse():
        gb.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    gb.build()
    return gb


async def test_code_file_count_matches_after_rehydration(async_session, tmp_path) -> None:
    repo = await insert_repo(async_session)
    (tmp_path / "app.py").write_text("import requests\nimport util\n\n\ndef run():\n    pass\n")
    (tmp_path / "util.py").write_text("def helper():\n    pass\n")
    live = _build(tmp_path)
    graph = live.graph()
    # The other typeless producers: a framework anchor, and a path an edge
    # names that traversal never parsed.
    graph.add_node("framework:django", language="external")
    graph.add_node("generated/schema.py")
    assert "external:requests" in graph

    await persist_graph_nodes(async_session, repo.id, live)
    await async_session.commit()
    rehydrated = await rehydrate_graph_builder(async_session, repo.id, tmp_path)

    assert _code_file_count(graph) == 2
    assert _code_file_count(rehydrated.graph()) == 2


async def test_entry_points_match_after_rehydration(async_session, tmp_path) -> None:
    repo = await insert_repo(async_session)
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "mod.py").write_text("def f():\n    pass\n")
    live = _build(tmp_path)
    graph = live.graph()
    # Paths an edge names that traversal never parsed, readable on disk: one
    # would declare a public API, the other a command.
    (tmp_path / "gen" / "__init__.py").write_text("from .mod import f\n")
    (tmp_path / "gen" / "cli.py").write_text('sub.add_parser("deploy")\n')
    graph.add_node("gen/__init__.py")
    graph.add_node("gen/cli.py")

    await persist_graph_nodes(async_session, repo.id, live)
    await async_session.commit()
    rehydrated = await rehydrate_graph_builder(async_session, repo.id, tmp_path)

    read = line_reader(tmp_path, None)
    for g in (graph, rehydrated.graph()):
        assert declared_entries(g, read) == []
        assert surfaces(g, read, tmp_path, ()).commands == []
