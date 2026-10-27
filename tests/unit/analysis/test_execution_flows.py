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


def test_trace_uses_dispatch_edges_and_rejects_unreliable_origins():
    graph = _chain_graph()
    graph.remove_edge("src/app.py::a", "src/app.py::b")
    graph.add_edge(
        "src/app.py::a",
        "src/app.py::b",
        edge_type="dispatches_to",
        confidence=0.9,
        resolution_origin="framework",
    )
    graph.add_edge(
        "src/app.py::a",
        "src/app.py::c",
        edge_type="calls",
        confidence=1.0,
        resolution_origin="global_unique",
    )
    report = trace_execution_flows(graph, {}, FlowConfig(min_flow_depth=1))
    main_flow = next(flow for flow in report.flows if flow.entry_point_id == "src/app.py::main")
    assert main_flow.trace[:4] == [
        "src/app.py::main",
        "src/app.py::a",
        "src/app.py::b",
        "src/app.py::c",
    ]


def _mention_chain(extra_edge_type: str | None, **extra_attrs) -> nx.DiGraph:
    """main -> load -> parse, with an optional non-call out-edge from parse."""
    g = nx.DiGraph()
    for nid, nm in [
        ("app.py::main", "main"),
        ("app.py::load", "load"),
        ("app.py::parse", "parse"),
        ("app.py::HANDLERS", "HANDLERS"),
    ]:
        _sym(g, nid, nm)
    g.add_edge(
        "app.py::main",
        "app.py::load",
        edge_type="calls",
        confidence=1.0,
        resolution_origin="import",
    )
    g.add_edge(
        "app.py::load",
        "app.py::parse",
        edge_type="calls",
        confidence=1.0,
        resolution_origin="same_file",
    )
    if extra_edge_type is not None:
        g.add_edge(
            "app.py::parse",
            "app.py::HANDLERS",
            edge_type=extra_edge_type,
            confidence=extra_attrs.get("confidence", 1.0),
            resolution_origin=extra_attrs.get("resolution_origin", "same_file"),
        )
    return g


def test_mention_out_edges_terminate_as_no_callees():
    """references / type_use are mentions, not declined execution successors."""
    for edge_type in (None, "references", "type_use"):
        report = trace_execution_flows(
            _mention_chain(edge_type), {}, FlowConfig(min_flow_depth=1, min_fan_out=1)
        )
        flow = next(f for f in report.flows if f.entry_point_id == "app.py::main")
        assert flow.termination == "no_callees"
        assert flow.termination_detail == {}


def test_weak_call_successor_still_confidence_filtered():
    """A real calls edge below the floor, or with an unreliable origin, is filtered."""
    for attrs, origin in [
        ({"confidence": 0.4, "resolution_origin": "same_file"}, "same_file"),
        ({"confidence": 1.0, "resolution_origin": "global_unique"}, "global_unique"),
    ]:
        report = trace_execution_flows(
            _mention_chain("calls", **attrs),
            {},
            FlowConfig(min_flow_depth=1, min_fan_out=1),
        )
        flow = next(f for f in report.flows if f.entry_point_id == "app.py::main")
        assert flow.termination == "confidence_filtered"
        assert flow.termination_detail == {origin: 1}


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


class TestWhatCountsAsAnExcludedNode:
    """Only symbol nodes carry ``file_path``; everything else falls back to its id.

    That fallback used to be the empty string, so a file node and an
    unresolved call target were never excluded however they were named —
    the opposite of what this filter is for.
    """

    @staticmethod
    def _graph(node_id: str, **attrs):
        import networkx as nx

        g = nx.DiGraph()
        g.add_node(node_id, **attrs)
        return g

    def test_a_symbol_node_is_judged_on_its_declared_file(self) -> None:
        from repowise.core.analysis.execution_flows import _is_excluded_node

        g = self._graph("tests/test_a.py::helper", file_path="tests/test_a.py")
        assert _is_excluded_node(g, "tests/test_a.py::helper")

        g = self._graph("src/main.py::run", file_path="src/main.py")
        assert not _is_excluded_node(g, "src/main.py::run")

    def test_a_file_node_is_judged_on_its_own_id(self) -> None:
        """A file node has no ``file_path`` attribute — its id is the path."""
        from repowise.core.analysis.execution_flows import _is_excluded_node

        assert _is_excluded_node(self._graph("scripts/build.py"), "scripts/build.py")
        assert _is_excluded_node(self._graph("tests/test_a.py"), "tests/test_a.py")
        assert not _is_excluded_node(self._graph("src/main.py"), "src/main.py")

    def test_an_unresolved_call_target_is_judged_on_its_bare_name(self) -> None:
        """A mis-resolved edge into a test fake is what this filter is for."""
        from repowise.core.analysis.execution_flows import _is_excluded_node

        assert _is_excluded_node(self._graph("test_helper"), "test_helper")
        assert not _is_excluded_node(self._graph("fetchall"), "fetchall")

    def test_an_external_is_not_a_path_and_is_never_excluded(self) -> None:
        from repowise.core.analysis.execution_flows import _is_excluded_node

        assert not _is_excluded_node(self._graph("external:pytest"), "external:pytest")
