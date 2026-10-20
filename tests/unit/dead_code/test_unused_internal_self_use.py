"""Regression tests for self-use and nested liveness in dead-code analysis."""

from __future__ import annotations

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph


def _private_nested_classes(edges: list) -> set[str]:
    """Unused-internal names for a C# file holding private nested ``Inner``/``Spare``."""
    g = _build_graph(
        nodes={
            "src/Outer.cs": {
                "language": "csharp",
                "symbols": [
                    {
                        "name": name,
                        "kind": "class",
                        "language": "csharp",
                        "visibility": "private",
                        "decorators": [],
                        "start_line": line,
                        "end_line": line + 4,
                    }
                    for name, line in (("Inner", 3), ("Spare", 9))
                ],
            },
        },
        edges=edges,
    )
    report = DeadCodeAnalyzer(g, git_meta_map={}).analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    return {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL}

def test_a_constructor_calling_itself_does_not_rescue_its_class():
    """A self-loop ``calls`` edge on the constructor is no use from outside."""
    ctor = "src/Outer.cs::Inner::Inner"
    edges = [(ctor, ctor, {"edge_type": "calls"})]
    assert _private_nested_classes(edges) == {"Inner", "Spare"}


def test_a_caller_inside_the_symbols_own_span_does_not_rescue_it():
    edges = [("src/Outer.cs::Inner", "src/Outer.cs::Inner", {"edge_type": "calls"})]
    assert _private_nested_classes(edges) == {"Inner", "Spare"}


def _two_private_functions(edges: list) -> set[str]:
    nodes = {
        "pkg/mod.py": {
            "symbols": [
                {
                    "name": name,
                    "kind": "function",
                    "visibility": "private",
                    "decorators": [],
                    "start_line": start,
                    "end_line": end,
                }
                for name, start, end in (("_outer", 1, 10), ("_inner", 3, 5))
            ],
        },
    }
    g = _build_graph(nodes=nodes, edges=edges)
    report = DeadCodeAnalyzer(g, git_meta_map={}).analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    return {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL}


def test_a_predecessor_declared_inside_the_candidate_span_is_not_a_use():
    edges = [("pkg/mod.py::_inner", "pkg/mod.py::_outer", {"edge_type": "calls"})]
    assert "_outer" in _two_private_functions(edges)


def test_a_caller_outside_the_candidate_span_still_counts_as_a_use():
    edges = [("pkg/mod.py::_outer", "pkg/mod.py::_inner", {"edge_type": "calls"})]
    assert "_inner" not in _two_private_functions(edges)


def test_a_caller_with_no_span_still_counts_as_a_use():
    g_edges = [("pkg/other.py::run", "pkg/mod.py::_outer", {"edge_type": "calls"})]
    assert "_outer" not in _two_private_functions(g_edges)


def _flagged(kind: DeadCodeKind, symbols: list, edges: list, other: bool = False) -> set[str]:
    """Findings of *kind* for ``pkg/mod.py`` holding *symbols* ``(name, vis, kind, start, end)``."""
    nodes = {
        "pkg/mod.py": {
            "symbols": [
                {
                    "name": name,
                    "kind": sym_kind,
                    "visibility": vis,
                    "decorators": [],
                    "start_line": start,
                    "end_line": end,
                }
                for name, vis, sym_kind, start, end in symbols
            ],
        },
    }
    if other:
        nodes["pkg/other.py"] = {
            "symbols": [
                {
                    "name": "run",
                    "kind": "function",
                    "visibility": "public",
                    "decorators": [],
                    "start_line": 1,
                    "end_line": 3,
                }
            ],
        }
    g = _build_graph(nodes=nodes, edges=edges)
    report = DeadCodeAnalyzer(g, git_meta_map={}).analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": True,
            "detect_zombie_packages": False,
            "min_confidence": 0.0,
        }
    )
    return {f.symbol_name for f in report.findings if f.kind == kind}


def test_a_live_nested_subclass_keeps_its_enclosing_class_used():
    symbols = [("_Node", "private", "class", 1, 20), ("_Leaf", "private", "class", 5, 10)]
    edges = [
        ("pkg/mod.py::_Leaf", "pkg/mod.py::_Node", {"edge_type": "extends"}),
        ("pkg/other.py::run", "pkg/mod.py::_Leaf", {"edge_type": "calls"}),
    ]
    assert "_Node" not in _flagged(DeadCodeKind.UNUSED_INTERNAL, symbols, edges, other=True)


def test_a_dead_nested_subclass_does_not_keep_its_enclosing_class_used():
    symbols = [("_Node", "private", "class", 1, 20), ("_Leaf", "private", "class", 5, 10)]
    edges = [("pkg/mod.py::_Leaf", "pkg/mod.py::_Node", {"edge_type": "extends"})]
    assert "_Node" in _flagged(DeadCodeKind.UNUSED_INTERNAL, symbols, edges)


def test_a_live_method_constructing_its_class_keeps_the_class_used():
    symbols = [("_K", "private", "class", 1, 20), ("make", "private", "function", 5, 8)]
    edges = [
        ("pkg/mod.py::make", "pkg/mod.py::_K", {"edge_type": "calls"}),
        ("pkg/other.py::run", "pkg/mod.py::make", {"edge_type": "calls"}),
    ]
    assert "_K" not in _flagged(DeadCodeKind.UNUSED_INTERNAL, symbols, edges, other=True)


def test_contained_symbols_with_the_same_span_calling_each_other_terminate():
    symbols = [
        ("_K", "private", "class", 1, 30),
        ("a", "private", "function", 5, 8),
        ("b", "private", "function", 5, 8),
    ]
    edges = [
        ("pkg/mod.py::a", "pkg/mod.py::b", {"edge_type": "calls"}),
        ("pkg/mod.py::b", "pkg/mod.py::a", {"edge_type": "calls"}),
        ("pkg/mod.py::a", "pkg/mod.py::_K", {"edge_type": "calls"}),
    ]
    assert "_K" in _flagged(DeadCodeKind.UNUSED_INTERNAL, symbols, edges)


def test_a_live_nested_public_subclass_keeps_its_exported_class_used():
    symbols = [("Node", "public", "class", 1, 20), ("Leaf", "public", "class", 5, 10)]
    edges = [
        ("pkg/mod.py::Leaf", "pkg/mod.py::Node", {"edge_type": "extends"}),
        ("pkg/other.py::run", "pkg/mod.py::Leaf", {"edge_type": "calls"}),
    ]
    assert "Node" not in _flagged(DeadCodeKind.UNUSED_EXPORT, symbols, edges, other=True)
