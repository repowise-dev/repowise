"""Static test-to-code reachability: the pure walks over an adjacency map.

A test file whose calls reach a source file, within ``max_depth`` hops,
*reaches* it. Pins the edge-type filter (containment must bridge files to
symbols and nothing else may), the ``resolution_origin`` filter, and the
measured depth default. The attributed reverse walk needs a database, so it
lives in ``tests/unit/persistence`` beside the session fixture.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.test_reachability import (
    DEFAULT_CALL_DEPTH,
    CallGraphView,
    call_graph_from_graph,
    files_reached_by_tests,
)


def _view(declares, calls):
    return CallGraphView(declares=declares, calls=calls)


# ---------------------------------------------------------------------------
# files_reached_by_tests: the batch "is anything testing this" walk
# ---------------------------------------------------------------------------


def test_a_test_that_calls_into_a_file_reaches_it():
    view = _view(
        {"tests/test_a.py": {"tests/test_a.py::test_x"}},
        {"tests/test_a.py::test_x": {"src/a.py::run"}},
    )
    assert files_reached_by_tests(view, {"tests/test_a.py"}) == {"src/a.py"}


def test_the_default_is_three_hops():
    """The call walk saturates at 3, so 3 is the default rather than a cap.

    Dogfooded against a real coverage run, hops 3, 4 and 5 returned the same 48
    claims and 44 confirmations: the recall ceiling is the call graph's own
    capture rate, not the depth.
    """
    assert DEFAULT_CALL_DEPTH == 3
    view = _view(
        {"t.py": {"t.py::test"}},
        {
            "t.py::test": {"a.py::f"},
            "a.py::f": {"b.py::g"},
            "b.py::g": {"c.py::h"},
            "c.py::h": {"d.py::i"},
        },
    )
    assert files_reached_by_tests(view, {"t.py"}) == {"a.py", "b.py", "c.py"}
    assert files_reached_by_tests(view, {"t.py"}, max_depth=4) == {
        "a.py",
        "b.py",
        "c.py",
        "d.py",
    }


def test_depth_bound_is_respected():
    view = _view(
        {"t.py": {"t.py::test"}},
        {"t.py::test": {"a.py::f"}, "a.py::f": {"b.py::g"}},
    )
    assert files_reached_by_tests(view, {"t.py"}, max_depth=1) == {"a.py"}
    assert files_reached_by_tests(view, {"t.py"}, max_depth=2) == {"a.py", "b.py"}


def test_test_files_are_excluded_from_the_result():
    # A test does not need a test, so a helper test module another test calls
    # must not come back as a file needing coverage.
    view = _view(
        {"tests/test_a.py": {"tests/test_a.py::test_x"}},
        {"tests/test_a.py::test_x": {"tests/helpers.py::build", "src/a.py::run"}},
    )
    reached = files_reached_by_tests(view, {"tests/test_a.py", "tests/helpers.py"})
    assert reached == {"src/a.py"}


def test_cycle_terminates():
    view = _view(
        {"t.py": {"t.py::test"}},
        {"t.py::test": {"a.py::f"}, "a.py::f": {"b.py::g"}, "b.py::g": {"a.py::f"}},
    )
    assert files_reached_by_tests(view, {"t.py"}, max_depth=10) == {"a.py", "b.py"}


def test_a_test_declaring_nothing_reaches_nothing():
    # No ``defines`` edge means no way into the symbol layer, which is the
    # documented "no signal" outcome rather than an error.
    view = _view({}, {"t.py::test": {"a.py::f"}})
    assert files_reached_by_tests(view, {"t.py"}) == set()


@pytest.mark.parametrize("tests,depth", [(set(), 2), ({"t.py"}, 0)])
def test_degenerate_inputs_return_empty(tests, depth):
    view = _view({"t.py": {"t.py::test"}}, {"t.py::test": {"a.py::f"}})
    assert files_reached_by_tests(view, tests, max_depth=depth) == set()


# ---------------------------------------------------------------------------
# call_graph_from_graph: the edge-type and resolution-origin filters
# ---------------------------------------------------------------------------


class _FakeGraph:
    def __init__(self, edges):
        self._edges = edges

    def edges(self, data=False):
        return [(s, t, d) for s, t, d in self._edges]


def test_containment_bridges_files_to_symbols_and_nothing_else():
    graph = _FakeGraph(
        [
            ("tests/test_a.py", "tests/test_a.py::test_x", {"edge_type": "defines"}),
            ("tests/test_a.py::test_x", "src/a.py::run", {"edge_type": "calls"}),
            # Not execution: an import is the weaker tier and is walked only by
            # the reverse fallback, and a co-change is history rather than code.
            ("tests/test_a.py", "src/b.py", {"edge_type": "imports"}),
            ("src/a.py", "src/c.py", {"edge_type": "co_changes"}),
            # Naming a symbol is not running it.
            ("src/a.py::run", "src/d.py::handler", {"edge_type": "references"}),
        ]
    )
    view = call_graph_from_graph(graph)
    assert view.declares == {"tests/test_a.py": ("tests/test_a.py::test_x",)}
    assert view.calls == {"tests/test_a.py::test_x": ("src/a.py::run",)}


def test_name_only_resolutions_are_dropped():
    """``global_unique`` binds a name to the only symbol carrying it: a guess.

    Dropping it cost no recall and bought 4.0 points of forward precision
    (91.7% -> 95.7%) on the dogfooded slice.
    """
    graph = _FakeGraph(
        [
            ("t.py::test", "a.py::f", {"edge_type": "calls", "resolution_origin": "global_unique"}),
            ("t.py::test", "b.py::g", {"edge_type": "calls", "resolution_origin": "same_file"}),
            # NULL means the row predates the vocabulary, not "unknown origin".
            ("t.py::test", "c.py::h", {"edge_type": "calls"}),
        ]
    )
    assert call_graph_from_graph(graph).calls == {"t.py::test": ("b.py::g", "c.py::h")}


def test_missing_graph_is_no_signal_not_an_error():
    view = call_graph_from_graph(None)
    assert view.declares == {} and view.calls == {}
