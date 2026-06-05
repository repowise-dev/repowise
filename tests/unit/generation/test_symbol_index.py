"""Symbol-index equivalence: indexed call-graph/heritage extraction must be
byte-identical to the historical full-node-scan path."""

from __future__ import annotations

import networkx as nx

from repowise.core.generation.context.graph_intelligence import (
    build_symbol_index,
    extract_call_graph,
    extract_heritage,
)


def _mixed_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    files = [f"pkg/f{i}.py" for i in range(6)]
    for fp in files:
        g.add_node(fp, language="python")
    # Symbol nodes spread across files, with call + heritage edges.
    for i, fp in enumerate(files):
        for j in range(4):
            g.add_node(
                f"{fp}::sym{j}",
                node_type="symbol",
                file_path=fp,
                name=f"sym{j}_{i}",
            )
    for i in range(5):
        g.add_edge(
            f"pkg/f{i}.py::sym0",
            f"pkg/f{i + 1}.py::sym1",
            edge_type="calls",
            confidence=0.9,
        )
        g.add_edge(
            f"pkg/f{i}.py::sym2",
            f"pkg/f{i + 1}.py::sym3",
            edge_type="extends",
        )
    # A non-call/heritage symbol edge and a file-level import edge (ignored).
    g.add_edge("pkg/f0.py::sym1", "pkg/f2.py::sym0", edge_type="references")
    g.add_edge("pkg/f0.py", "pkg/f1.py", imported_names=["x"])
    return g


def test_indexed_extraction_matches_scan() -> None:
    g = _mixed_graph()
    index = build_symbol_index(g)

    for fp in [f"pkg/f{i}.py" for i in range(6)] + ["missing.py"]:
        assert extract_call_graph(fp, g, index) == extract_call_graph(fp, g)
        assert extract_heritage(fp, g, index) == extract_heritage(fp, g)


def test_index_buckets_only_symbol_nodes() -> None:
    g = _mixed_graph()
    index = build_symbol_index(g)
    assert set(index) == {f"pkg/f{i}.py" for i in range(6)}
    assert all(len(nodes) == 4 for nodes in index.values())
    # Bucket order matches graph node iteration order.
    assert [n for n, _ in index["pkg/f0.py"]] == [
        "pkg/f0.py::sym0",
        "pkg/f0.py::sym1",
        "pkg/f0.py::sym2",
        "pkg/f0.py::sym3",
    ]


def test_assemble_file_page_identical_with_index() -> None:
    from repowise.core.generation.context_assembler import ContextAssembler
    from repowise.core.generation.models import GenerationConfig
    from repowise.core.ingestion.models import ParsedFile

    from .conftest import _make_file_info, _make_symbol

    g = _mixed_graph()
    path = "pkg/f1.py"
    parsed = ParsedFile(
        file_info=_make_file_info(path=path),
        symbols=[_make_symbol(name="sym0_1", file_path=path)],
        imports=[],
        exports=["sym0_1"],
        docstring="d",
        parse_errors=[],
        content_hash="h",
    )
    assembler = ContextAssembler(GenerationConfig())
    index = build_symbol_index(g)

    plain = assembler.assemble_file_page(parsed, g, {}, {}, {}, b"x = 1\n")
    indexed = assembler.assemble_file_page(
        parsed, g, {}, {}, {}, b"x = 1\n", symbol_index=index
    )
    assert plain == indexed
