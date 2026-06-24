"""Tests for the Move Method refactoring detector (feature envy).

The detector reads the method/class membership and ``calls`` edges off the
in-memory graph (surfaced on ``RefactoringContext.graph``) and suggests
moving a method to the class it actually uses. Fixtures build a tiny NetworkX
graph directly so the entity sets — which class owns which member, who calls
whom — are explicit and the expected suggestion is unambiguous.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.health.refactoring import (
    RefactoringContext,
    detect_refactorings,
)
from repowise.core.analysis.health.refactoring.move_method import MoveMethodDetector


def _add_class(g: nx.DiGraph, file_path: str, cls: str, methods: list[str]) -> None:
    if file_path not in g:
        g.add_node(file_path, node_type="file")
    class_id = f"{file_path}::{cls}"
    g.add_node(class_id, node_type="symbol", kind="class", name=cls, file_path=file_path)
    for m in methods:
        mid = f"{file_path}::{cls}.{m}"
        g.add_node(
            mid,
            node_type="symbol",
            kind="method",
            name=m,
            parent_name=cls,
            file_path=file_path,
            start_line=1,
            end_line=20,
        )
        g.add_edge(class_id, mid, edge_type="has_method")
        g.add_edge(file_path, mid, edge_type="defines")


def _call(g: nx.DiGraph, src: str, dst: str) -> None:
    g.add_edge(src, dst, edge_type="calls")


def _ctx(g: nx.DiGraph, file_path: str) -> RefactoringContext:
    return RefactoringContext(file_path=file_path, language="python", nloc=40, graph=g)


def _detect(g: nx.DiGraph, file_path: str) -> list:
    return [
        s for s in detect_refactorings(_ctx(g, file_path)) if s.refactoring_type == "move_method"
    ]


def _envy_graph() -> nx.DiGraph:
    """C.envious calls 3 of T's members and 0 of its own — textbook envy."""
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["envious", "helper"])
    _add_class(g, "t.py", "T", ["alpha", "beta", "gamma"])
    for m in ("alpha", "beta", "gamma"):
        _call(g, "c.py::C.envious", f"t.py::T.{m}")
    return g


def test_feature_envy_suggests_move_to_target_class():
    out = _detect(_envy_graph(), "c.py")
    assert len(out) == 1
    s = out[0]
    assert s.refactoring_type == "move_method"
    assert s.target_symbol == "C.envious"
    assert s.plan == {
        "method": "envious",
        "from_class": "C",
        "to_class": "T",
        "to_file": "t.py",
    }
    assert s.evidence["foreign_calls"] == 3
    assert s.evidence["own_calls"] == 0
    assert s.evidence["target_distance"] < s.evidence["own_distance"]
    assert s.confidence == "high"


def test_method_that_uses_its_own_class_is_not_envious():
    g = _envy_graph()
    # envious now also calls 2 of its own class's members → no longer clearly
    # foreign-leaning (own_calls above the max-own gate).
    _add_class(g, "c.py", "C", ["envious", "helper", "h2"])
    _call(g, "c.py::C.envious", "c.py::C.helper")
    _call(g, "c.py::C.envious", "c.py::C.h2")
    assert _detect(g, "c.py") == []


def test_single_foreign_call_below_threshold_is_ignored():
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["m"])
    _add_class(g, "t.py", "T", ["alpha", "beta"])
    _call(g, "c.py::C.m", "t.py::T.alpha")  # only one foreign member
    assert _detect(g, "c.py") == []


def test_dunder_methods_never_move():
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["__init__"])
    _add_class(g, "t.py", "T", ["alpha", "beta", "gamma"])
    for m in ("alpha", "beta", "gamma"):
        _call(g, "c.py::C.__init__", f"t.py::T.{m}")
    assert _detect(g, "c.py") == []


def test_nearest_of_two_foreign_classes_wins():
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["m"])
    _add_class(g, "t.py", "T", ["a", "b", "c"])
    _add_class(g, "u.py", "U", ["x", "y", "z", "w", "v"])
    # m touches all 3 of T (small class → distance 0) but only 2 of U's 5.
    for member in ("a", "b", "c"):
        _call(g, "c.py::C.m", f"t.py::T.{member}")
    for member in ("x", "y"):
        _call(g, "c.py::C.m", f"u.py::U.{member}")
    out = _detect(g, "c.py")
    assert len(out) == 1
    assert out[0].plan["to_class"] == "T"


def test_no_graph_yields_no_suggestions():
    ctx = RefactoringContext(file_path="c.py", language="python", nloc=40, graph=None)
    assert MoveMethodDetector().detect(ctx) == []


def test_calling_two_methods_of_a_huge_class_is_not_envy():
    # A method that touches 2 members of a 40-member god class is far from it
    # (Jaccard distance ~0.95) — normal collaboration, not envy.
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["m"])
    _add_class(g, "big.py", "Big", [f"meth{i}" for i in range(40)])
    _call(g, "c.py::C.m", "big.py::Big.meth0")
    _call(g, "c.py::C.m", "big.py::Big.meth1")
    assert _detect(g, "c.py") == []


def test_method_in_test_file_is_skipped():
    # A test method exercising the class under test is not a move candidate.
    g = nx.DiGraph()
    _add_class(g, "tests/test_thing.py", "ThingTest", ["test_it"])
    _add_class(g, "t.py", "T", ["alpha", "beta", "gamma"])
    for m in ("alpha", "beta", "gamma"):
        _call(g, "tests/test_thing.py::ThingTest.test_it", f"t.py::T.{m}")
    assert _detect(g, "tests/test_thing.py") == []


def test_target_in_test_file_is_rejected():
    # Never propose moving production code into a test class.
    g = nx.DiGraph()
    _add_class(g, "c.py", "C", ["m"])
    _add_class(g, "tests/test_t.py", "T", ["alpha", "beta", "gamma"])
    for m in ("alpha", "beta", "gamma"):
        _call(g, "c.py::C.m", f"tests/test_t.py::T.{m}")
    assert _detect(g, "c.py") == []


def test_deterministic_and_stable_order():
    g = _envy_graph()
    # A second envious method in the same file → two suggestions, stable order.
    _add_class(g, "c.py", "C", ["envious", "envious2", "helper"])
    g.add_node(
        "c.py::C.envious2",
        node_type="symbol",
        kind="method",
        name="envious2",
        parent_name="C",
        file_path="c.py",
        start_line=1,
        end_line=20,
    )
    g.add_edge("c.py::C", "c.py::C.envious2", edge_type="has_method")
    g.add_edge("c.py", "c.py::C.envious2", edge_type="defines")
    for m in ("alpha", "beta", "gamma"):
        _call(g, "c.py::C.envious2", f"t.py::T.{m}")
    first = [s.target_symbol for s in _detect(g, "c.py")]
    second = [s.target_symbol for s in _detect(g, "c.py")]
    assert first == second
    assert len(first) == 2
