"""Tests for execution-flow tracing (analysis/execution_flows)."""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.execution_flows import (
    FlowConfig,
    trace_execution_flows,
)


def _sym(g: nx.DiGraph, node_id: str, name: str, **kw) -> None:
    g.add_node(
        node_id,
        node_type="symbol",
        kind="function",
        name=name,
        file_path=node_id.split("::", 1)[0],
        visibility="public",
        **kw,
    )


def _call(g: nx.DiGraph, src: str, dst: str, confidence: float = 1.0) -> None:
    g.add_edge(src, dst, edge_type="calls", confidence=confidence)


def _chain_graph() -> nx.DiGraph:
    """main -> a -> b -> c, with a couple of leaf calls to give fan-out."""
    g = nx.DiGraph()
    for nid, nm in [
        ("src/app.py::main", "main"),
        ("src/app.py::a", "a"),
        ("src/app.py::b", "b"),
        ("src/app.py::c", "c"),
        ("src/util.py::leaf1", "leaf1"),
        ("src/util.py::leaf2", "leaf2"),
    ]:
        _sym(g, nid, nm)
    _call(g, "src/app.py::main", "src/app.py::a")
    _call(g, "src/app.py::main", "src/util.py::leaf1")
    _call(g, "src/app.py::a", "src/app.py::b")
    _call(g, "src/app.py::a", "src/util.py::leaf2")
    _call(g, "src/app.py::b", "src/app.py::c")
    return g


def test_all_candidates_scored_are_exposed():
    g = _chain_graph()
    report = trace_execution_flows(g, {}, FlowConfig())
    # main, a, b all have fan-out >= 2 / >= 1 and score above threshold.
    assert report.entry_point_scores  # populated for persistence
    assert "src/app.py::main" in report.entry_point_scores
    # Every scored candidate is represented, not just the traced flows.
    assert len(report.entry_point_scores) >= report.total_flows


def test_trace_follows_primary_chain():
    g = _chain_graph()
    report = trace_execution_flows(g, {}, FlowConfig(min_flow_depth=1))
    main_flow = next(
        f for f in report.flows if f.entry_point_id == "src/app.py::main"
    )
    # Primary path follows the highest-fan-out successor at each hop.
    assert main_flow.trace[:4] == [
        "src/app.py::main",
        "src/app.py::a",
        "src/app.py::b",
        "src/app.py::c",
    ]


def test_trace_skips_test_nodes():
    """A call edge into a test fake must not leak into the trace."""
    g = _chain_graph()
    _sym(g, "tests/unit/test_app.py::_FakeResult", "_FakeResult")
    # b "calls" a test fake (mis-resolved edge); it must be skipped.
    _call(g, "src/app.py::b", "tests/unit/test_app.py::_FakeResult")
    report = trace_execution_flows(g, {}, FlowConfig(min_flow_depth=1))
    for flow in report.flows:
        assert not any("tests/" in node for node in flow.trace)


def test_min_flow_depth_filters_trivial_flows():
    """A lone single-call entry point is not reported as a flow by default."""
    g = nx.DiGraph()
    _sym(g, "src/x.py::solo", "solo")
    _sym(g, "src/x.py::one", "one")
    _sym(g, "src/x.py::two", "two")
    # solo has fan-out 2 (qualifies as a candidate) but no deeper chain.
    _call(g, "src/x.py::solo", "src/x.py::one")
    _call(g, "src/x.py::solo", "src/x.py::two")
    default = trace_execution_flows(g, {}, FlowConfig())
    assert default.total_flows == 0  # depth-1 flow dropped
    # ...but the candidate is still scored and persistable.
    assert "src/x.py::solo" in default.entry_point_scores
    permissive = trace_execution_flows(g, {}, FlowConfig(min_flow_depth=1))
    assert permissive.total_flows == 1


def test_test_files_never_score_as_entry_points():
    g = nx.DiGraph()
    _sym(g, "tests/unit/test_app.py::helper", "helper")
    _sym(g, "tests/unit/test_app.py::a", "a")
    _sym(g, "tests/unit/test_app.py::b", "b")
    _call(g, "tests/unit/test_app.py::helper", "tests/unit/test_app.py::a")
    _call(g, "tests/unit/test_app.py::helper", "tests/unit/test_app.py::b")
    report = trace_execution_flows(g, {}, FlowConfig(min_flow_depth=1))
    assert report.total_entry_points_scored == 0
    assert report.entry_point_scores == {}
