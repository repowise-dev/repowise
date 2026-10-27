"""The shared symbol-to-file projection and its guards."""

from __future__ import annotations

from repowise.server.mcp_server._graph_files import (
    file_ext,
    is_symbol_node,
    keep_projected_edge,
    node_to_file,
)


def test_node_to_file_projects_symbols_and_passes_files_through():
    assert node_to_file("a/b.py::Klass.meth") == "a/b.py"
    assert node_to_file("a/b.py") == "a/b.py"
    assert is_symbol_node("a/b.py::X") and not is_symbol_node("a/b.py")


def test_file_ext():
    assert file_ext("a/b/retrieval.py") == "py"
    assert file_ext("a/b/Makefile") == ""


def test_guards():
    assert keep_projected_edge("a.py", "b.py", "calls", 0.9)
    # Below the floor, same file, and across a language boundary.
    assert not keep_projected_edge("a.py", "b.py", "calls", 0.2)
    assert not keep_projected_edge("a.py", "a.py", "calls", 0.9)
    assert not keep_projected_edge("a.py", "b.ts", "calls", 0.9)
    # The floor is scoped to ``calls``; nothing else carries a confidence tail.
    assert keep_projected_edge("a.py", "b.py", "extends", 0.2)
