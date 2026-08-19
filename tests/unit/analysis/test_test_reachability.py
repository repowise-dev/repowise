"""Static test-to-code reachability: the pure walks over an adjacency map.

A test file that imports a source file, directly or within ``max_depth`` hops,
*reaches* it. Pins the edge-type filter (containment must not leak the walk into
symbol nodes) and the measured depth default. The attributed reverse walk needs
a database, so it lives in ``tests/unit/persistence`` beside the session
fixture.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.test_reachability import (
    DEFAULT_MAX_DEPTH,
    files_reached_by_tests,
    forward_dependencies_from_graph,
)

# ---------------------------------------------------------------------------
# files_reached_by_tests: the batch "is anything testing this" walk
# ---------------------------------------------------------------------------


def test_direct_import_is_reached():
    fwd = {"tests/test_a.py": {"src/a.py"}}
    assert files_reached_by_tests(fwd, {"tests/test_a.py"}) == {"src/a.py"}


def test_the_default_is_one_hop():
    """A second hop is opt-in, because it was measured to cost more than it buys.

    Dogfooded against a real coverage run, depth 2 halved reach precision
    (72.1% -> 40.7%) and cut run-list precision from 93.1% to 73.8%. Repos whose
    tests import a package facade pass ``max_depth=2`` explicitly.
    """
    assert DEFAULT_MAX_DEPTH == 1
    fwd = {"tests/test_a.py": {"src/__init__.py"}, "src/__init__.py": {"src/a.py"}}
    assert files_reached_by_tests(fwd, {"tests/test_a.py"}) == {"src/__init__.py"}
    assert files_reached_by_tests(fwd, {"tests/test_a.py"}, max_depth=2) == {
        "src/__init__.py",
        "src/a.py",
    }


def test_depth_bound_is_respected():
    fwd = {"t.py": {"a.py"}, "a.py": {"b.py"}, "b.py": {"c.py"}}
    assert files_reached_by_tests(fwd, {"t.py"}, max_depth=1) == {"a.py"}
    assert files_reached_by_tests(fwd, {"t.py"}, max_depth=2) == {"a.py", "b.py"}
    assert files_reached_by_tests(fwd, {"t.py"}, max_depth=3) == {"a.py", "b.py", "c.py"}


def test_test_files_are_excluded_from_the_result():
    # A test does not need a test, so a helper test module that another test
    # imports must not come back as a file needing coverage.
    fwd = {"tests/test_a.py": {"tests/helpers.py", "src/a.py"}}
    reached = files_reached_by_tests(fwd, {"tests/test_a.py", "tests/helpers.py"})
    assert reached == {"src/a.py"}


def test_cycle_terminates():
    fwd = {"t.py": {"a.py"}, "a.py": {"b.py"}, "b.py": {"a.py"}}
    assert files_reached_by_tests(fwd, {"t.py"}, max_depth=10) == {"a.py", "b.py"}


@pytest.mark.parametrize("tests,depth", [(set(), 2), ({"t.py"}, 0)])
def test_degenerate_inputs_return_empty(tests, depth):
    assert files_reached_by_tests({"t.py": {"a.py"}}, tests, max_depth=depth) == set()


# ---------------------------------------------------------------------------
# forward_dependencies_from_graph: the edge-type filter
# ---------------------------------------------------------------------------


class _FakeGraph:
    def __init__(self, edges):
        self._edges = edges

    def edges(self, data=False):
        return [(s, t, d) for s, t, d in self._edges]


def test_only_file_dependency_edges_are_followed():
    # ``defines`` is the one that matters: with containment in, the walk leaves
    # the file layer into symbol nodes, and every file defining a called symbol
    # reads as a dependency of the caller's file.
    graph = _FakeGraph(
        [
            ("tests/test_a.py", "src/a.py", {"edge_type": "imports"}),
            ("tests/test_a.py", "tests/test_a.py::test_x", {"edge_type": "defines"}),
            ("src/a.py", "src/b.py", {"edge_type": "co_changes"}),
            ("src/a.py", "src/c.py", {"edge_type": "type_use"}),
        ]
    )
    fwd = forward_dependencies_from_graph(graph)
    assert fwd == {"tests/test_a.py": {"src/a.py"}, "src/a.py": {"src/c.py"}}


def test_missing_graph_is_no_signal_not_an_error():
    assert forward_dependencies_from_graph(None) == {}
