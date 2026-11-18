"""The shared symbol-to-file projection and its guards."""

from __future__ import annotations

from repowise.server.mcp_server._graph_files import (
    file_ext,
    is_symbol_node,
    keep_projected_edge,
)


def test_is_symbol_node():
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
