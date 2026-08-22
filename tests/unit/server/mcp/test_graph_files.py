"""The shared symbol-to-file projection and its guards."""

from __future__ import annotations

from repowise.server.mcp_server._graph_files import (
    PATHS_PER_QUERY,
    batched_paths,
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


def test_batched_paths_stays_under_the_sqlite_expression_depth():
    """A candidate set of a few hundred files raised OperationalError unbatched."""
    paths = [f"src/mod{i}.py" for i in range(PATHS_PER_QUERY * 2 + 7)]
    batches = list(batched_paths(paths))
    assert all(len(b) <= PATHS_PER_QUERY for b in batches)
    assert sorted(p for b in batches for p in b) == sorted(paths)
    # Deterministic order, because which neighbours a truncated walk saw must
    # not move with the per-process hash seed.
    assert list(batched_paths(set(paths))) == batches
