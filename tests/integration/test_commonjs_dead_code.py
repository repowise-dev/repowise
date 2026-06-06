"""End-to-end guard for CommonJS property-access resolution (#295).

A whole-module ``require`` followed by ``svc.used()`` must register as a use of
``used`` (so it is not a dead-code false positive), and the resolution must be
*targeted* — it must not phantom-mark every export of the required module as
used.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

ENTRY_JS = """const svc = require('./svc.js');

function main() {
  return svc.used();
}

module.exports = { main };
"""

SVC_JS = """function used() {
  return 1;
}

function reallyDead() {
  return 2;
}

module.exports = { used, reallyDead };
"""


@pytest.fixture(scope="module")
def graph_and_report(tmp_path_factory):
    repo = tmp_path_factory.mktemp("cjs_sample")
    (repo / "entry.js").write_text(ENTRY_JS, encoding="utf-8")
    (repo / "svc.js").write_text(SVC_JS, encoding="utf-8")

    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        source = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, source))
    graph = builder.build()
    report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze({"min_confidence": 0.0})
    return graph, report


def _calls_to(graph, target: str) -> bool:
    return any(
        graph[u][target].get("edge_type") == "calls" for u in graph.predecessors(target)
    )


def test_property_access_call_resolves_to_required_export(graph_and_report):
    """svc.used() must create a `calls` edge to svc.js::used (the fix)."""
    graph, _ = graph_and_report
    assert "svc.js::used" in graph
    assert _calls_to(graph, "svc.js::used")


def test_used_function_is_not_flagged_dead(graph_and_report):
    """The actively-used export must not appear as a dead-code finding."""
    _, report = graph_and_report
    assert "used" not in {f.symbol_name for f in report.findings}


def test_resolution_is_targeted_not_whole_module(graph_and_report):
    """Only the accessed member resolves — `reallyDead` must NOT get a phantom
    `calls` edge just because the whole module was required."""
    graph, _ = graph_and_report
    assert "svc.js::reallyDead" in graph
    assert not _calls_to(graph, "svc.js::reallyDead")


# ---------------------------------------------------------------------------
# Chained CJS re-exports: index.js -> lib/api.js -> lib/impl.js
# ---------------------------------------------------------------------------

INDEX_JS = """'use strict';
module.exports = require('./lib/api');
"""

API_JS = """'use strict';
Object.assign(module.exports, require('./impl'));
"""

IMPL_JS = """function realWork() {
  return 42;
}

module.exports = { realWork };
"""


@pytest.fixture(scope="module")
def chained_graph(tmp_path_factory):
    repo = tmp_path_factory.mktemp("cjs_chain")
    (repo / "index.js").write_text(INDEX_JS, encoding="utf-8")
    lib = repo / "lib"
    lib.mkdir()
    (lib / "api.js").write_text(API_JS, encoding="utf-8")
    (lib / "impl.js").write_text(IMPL_JS, encoding="utf-8")

    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        source = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, source))
    return builder.build()


def test_reexport_chain_produces_import_edges(chained_graph):
    """module.exports = require(...) / Object.assign(module.exports, ...)
    must produce real import edges (express's 2.0-edges/file gap)."""
    assert chained_graph.has_edge("index.js", "lib/api.js")
    assert chained_graph["index.js"]["lib/api.js"]["edge_type"] == "imports"
    assert chained_graph.has_edge("lib/api.js", "lib/impl.js")


def test_reexport_chain_reaches_the_implementation(chained_graph):
    """BFS from the package root must transit the whole a -> b -> c chain."""
    import networkx as nx

    assert nx.has_path(chained_graph, "index.js", "lib/impl.js")
