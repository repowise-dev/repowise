"""Tests for `_has_importing_test_file` -- the import-graph fallback for
`has_test_file` that catches a test with no name-based pairing at all (one
test program exercising several source files together, e.g. a Delphi
`TestFoo.dpr` that `uses`/`in`-imports several unrelated-stem `.pas` units)."""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.health.engine import _has_importing_test_file


def test_true_when_a_test_node_imports_the_file() -> None:
    g = nx.DiGraph()
    g.add_node("src/tools/TestDualPanelCmdLine.dpr", is_test=True)
    g.add_node("src/Core/uDualPanelCmd.pas", is_test=False)
    g.add_edge("src/tools/TestDualPanelCmdLine.dpr", "src/Core/uDualPanelCmd.pas")

    assert _has_importing_test_file("src/Core/uDualPanelCmd.pas", g)


def test_false_when_only_non_test_files_import_it() -> None:
    g = nx.DiGraph()
    g.add_node("src/Core/uMainForm.pas", is_test=False)
    g.add_node("src/Core/uDualPanelCmd.pas", is_test=False)
    g.add_edge("src/Core/uMainForm.pas", "src/Core/uDualPanelCmd.pas")

    assert not _has_importing_test_file("src/Core/uDualPanelCmd.pas", g)


def test_false_when_file_has_no_importers() -> None:
    g = nx.DiGraph()
    g.add_node("src/Core/uOrphan.pas", is_test=False)

    assert not _has_importing_test_file("src/Core/uOrphan.pas", g)


def test_false_when_file_not_in_graph() -> None:
    g = nx.DiGraph()
    g.add_node("src/Core/uOther.pas", is_test=False)

    assert not _has_importing_test_file("src/Core/uMissing.pas", g)


def test_false_when_graph_is_none() -> None:
    assert not _has_importing_test_file("src/Core/uAnything.pas", None)


def test_true_when_one_of_several_importers_is_a_test() -> None:
    # A file imported by both production code and a test program.
    g = nx.DiGraph()
    g.add_node("src/Core/uMainForm.pas", is_test=False)
    g.add_node("src/tools/TestVfsRegistry.dpr", is_test=True)
    g.add_node("src/Core/uVfsRegistry.pas", is_test=False)
    g.add_edge("src/Core/uMainForm.pas", "src/Core/uVfsRegistry.pas")
    g.add_edge("src/tools/TestVfsRegistry.dpr", "src/Core/uVfsRegistry.pas")

    assert _has_importing_test_file("src/Core/uVfsRegistry.pas", g)
