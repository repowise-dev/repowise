"""Unit tests for C/C++ header ↔ implementation pairing edges."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()

class TestCppHeaderSourcePairing:
    def test_header_source_pair_links_both_directions(self, tmp_path: Path) -> None:
        (tmp_path / "util.h").write_text("int helper(void);\n")
        (tmp_path / "util.c").write_text('#include "util.h"\nint helper(void) { return 1; }\n')
        (tmp_path / "main.c").write_text('#include "util.h"\nint main(void) { return helper(); }\n')
        graph = _build(tmp_path)
        # main.c -> util.h (include), util.c -> util.h (include),
        # util.h -> util.c (pairing) — the implementation is reachable
        # from any consumer of the header.
        assert graph.has_edge("main.c", "util.h")
        pair = graph.get_edge_data("util.h", "util.c")
        assert pair is not None
        assert pair.get("hint_source") == "header_source_pair"
        assert nx.has_path(graph, "main.c", "util.c")

    def test_different_stems_not_paired(self, tmp_path: Path) -> None:
        (tmp_path / "util.h").write_text("int helper(void);\n")
        (tmp_path / "other.c").write_text("int x(void) { return 0; }\n")
        graph = _build(tmp_path)
        assert not graph.has_edge("util.h", "other.c")

    def test_different_dirs_not_paired(self, tmp_path: Path) -> None:
        (tmp_path / "include").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "include" / "util.h").write_text("int helper(void);\n")
        (tmp_path / "src" / "util.c").write_text("int helper(void) { return 1; }\n")
        graph = _build(tmp_path)
        # Conservative: same-dir only (recorded cut — include/ vs src/
        # layouts pair through the target fan-out instead).
        assert not graph.has_edge("include/util.h", "src/util.c")
