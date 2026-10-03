"""A file never depends on itself: no file-node self-loop survives a build.

A package.json that exports itself (``"./package.json": "./package.json"``,
common so tools can read the manifest) used to become a package.json →
package.json edge, and PageRank fed the node its own rank.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.dynamic_hints import HintRegistry
from repowise.core.ingestion.dynamic_hints.node import NodeDynamicHints

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"


def _build(repo: Path) -> GraphBuilder:
    builder = GraphBuilder(repo_path=repo)
    parser = ASTParser()
    paths = []
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
        paths.append(fi.path)
    builder.build()
    builder.add_dynamic_edges(
        HintRegistry().extract_all(repo, dotnet_index=builder.dotnet_index, file_paths=paths)
    )
    return builder


def _file_self_loops(g) -> list[str]:
    return [
        u
        for u, v in g.edges()
        if u == v and g.nodes[u].get("node_type", "file") in ("file", "external")
    ]


def test_self_export_adds_no_self_loop_and_no_pagerank_lift() -> None:
    repo = FIXTURES / "node_pkg_self_export"
    hinted = {(e.source, e.target) for e in NodeDynamicHints().extract(repo)}
    # The fixture really does produce the self-export hint the builder must drop.
    assert ("package.json", "package.json") in hinted
    builder = _build(repo)
    assert builder.graph().has_edge("package.json", "src/index.js")
    assert _file_self_loops(builder.graph()) == []
    assert _file_self_loops(builder.file_subgraph()) == []
    pr = builder.pagerank()
    # The manifest only points out; with no inbound edge it must not outrank
    # the entry file it points at.
    assert pr["package.json"] < pr["src/index.js"]


@pytest.mark.parametrize("fixture", ["sample_repo", "ts_sample", "go_sample"])
def test_no_file_self_loops_after_build(fixture: str) -> None:
    builder = _build(FIXTURES / fixture)
    assert builder.graph().number_of_nodes() > 0
    assert _file_self_loops(builder.graph()) == []
    assert _file_self_loops(builder.file_subgraph()) == []
