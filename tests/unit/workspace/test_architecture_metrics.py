"""Tests for workspace architecture-complexity metrics.

The numeric checks are hand-computed against the visibility matrix so the score
formula and the propagation-cost normalization can't drift silently. The fixture
shapes are the canonical complexity cases: a pure layered DAG, a fully cyclic
graph, and a core-periphery system with one service in each role.
"""

from __future__ import annotations

from repowise.core.workspace.architecture_metrics import (
    ARCH_CORE_PERIPHERY,
    ARCH_HIERARCHICAL,
    ROLE_CONTROL,
    ROLE_CORE,
    ROLE_PERIPHERAL,
    ROLE_SHARED,
    SCORE_MAX,
    architecture_score,
    compute_architecture_metrics,
)
from repowise.core.workspace.system_graph import SystemEdge, SystemGraph, SystemNode

# ---------------------------------------------------------------------------
# Fixture builders (mirrors test_conformance.py)
# ---------------------------------------------------------------------------


def _node(node_id, repo=None, name=None, service_path=None) -> SystemNode:
    return SystemNode(
        id=node_id,
        repo=repo or node_id.split("::", 1)[0],
        service_path=service_path,
        name=name or node_id,
    )


def _edge(source, target, kind="http", structural=True, match="exact") -> SystemEdge:
    return SystemEdge(
        id=f"{source}->{target}:{kind}",
        source=source,
        target=target,
        kind=kind,
        match_type=match,
        confidence=1.0,
        weight=1,
        structural=structural,
    )


def _graph(nodes, edges) -> SystemGraph:
    return SystemGraph(nodes=[_node(n) for n in nodes], edges=edges)


def _roles_by_id(metrics):
    return {r.id: r for r in metrics.roles}


# ---------------------------------------------------------------------------
# Pure layered DAG: A -> B -> C
# ---------------------------------------------------------------------------


def test_layered_dag_low_coupling_high_score():
    graph = _graph(["A", "B", "C"], [_edge("A", "B"), _edge("B", "C")])
    m = compute_architecture_metrics(graph)

    # Visibility (incl self): A->{A,B,C}=3, B->{B,C}=2, C->{C}=1. Total 6.
    # Off-diagonal = 6 - 3 = 3; denominator = 3*2 = 6; pc = 0.5.
    assert m.propagation_cost == 0.5
    assert m.propagation_cost_pct == 50.0
    # No cyclic group.
    assert m.core_size == 0
    assert m.core_members == []
    assert m.cycle_count == 0
    assert m.architecture_type == ARCH_HIERARCHICAL
    # score = 10 - 5*0.5 - 0 - 0 - 0 = 7.5
    assert m.score == 7.5

    roles = _roles_by_id(m)
    # A reaches B,C but nothing reaches A -> high fan-out, low fan-in -> control.
    assert roles["A"].role == ROLE_CONTROL
    # C is reached by A,B but reaches nothing else -> shared.
    assert roles["C"].role == ROLE_SHARED


# ---------------------------------------------------------------------------
# Fully cyclic: A -> B -> C -> A
# ---------------------------------------------------------------------------


def test_fully_cyclic_high_coupling_low_score():
    graph = _graph(
        ["A", "B", "C"],
        [_edge("A", "B"), _edge("B", "C"), _edge("C", "A")],
    )
    m = compute_architecture_metrics(graph)

    # Everything reaches everything: pc = 1.0.
    assert m.propagation_cost == 1.0
    assert m.core_size == 3
    assert m.core_members == ["A", "B", "C"]
    assert m.core_ratio == 1.0
    assert m.cycle_count == 1
    assert m.architecture_type == ARCH_CORE_PERIPHERY
    # score = 10 - 5*1.0 - 3*1.0 - min(2, 0.5*1) = 1.5
    assert m.score == 1.5
    assert all(r.role == ROLE_CORE for r in m.roles)


def test_cyclic_scores_below_layered():
    dag = compute_architecture_metrics(_graph(["A", "B", "C"], [_edge("A", "B"), _edge("B", "C")]))
    cyclic = compute_architecture_metrics(
        _graph(["A", "B", "C"], [_edge("A", "B"), _edge("B", "C"), _edge("C", "A")])
    )
    assert cyclic.score < dag.score


# ---------------------------------------------------------------------------
# Core-periphery: cyclic core {A,B,C} + shared lib L + control gateway G +
# peripheral leaf P. One service in each role.
# ---------------------------------------------------------------------------


def _core_periphery_graph() -> SystemGraph:
    return _graph(
        ["A", "B", "C", "L", "G", "P"],
        [
            _edge("A", "B"),
            _edge("B", "C"),
            _edge("C", "A"),  # cyclic core
            _edge("A", "L"),  # core depends on shared lib
            _edge("G", "A"),  # gateway calls into the core
            _edge("G", "P"),  # gateway calls a peripheral leaf
        ],
    )


def test_core_periphery_roles_and_type():
    m = compute_architecture_metrics(_core_periphery_graph())

    assert m.core_members == ["A", "B", "C"]
    assert m.core_size == 3
    assert m.architecture_type == ARCH_CORE_PERIPHERY

    roles = _roles_by_id(m)
    assert roles["A"].role == ROLE_CORE
    assert roles["B"].role == ROLE_CORE
    assert roles["C"].role == ROLE_CORE
    # L: reached by the whole core + gateway, depends on nothing -> shared.
    assert roles["L"].role == ROLE_SHARED
    # G: reaches the whole system, nothing reaches it -> control.
    assert roles["G"].role == ROLE_CONTROL
    # P: only the gateway reaches it, it reaches nothing -> peripheral.
    assert roles["P"].role == ROLE_PERIPHERAL

    assert m.role_breakdown() == {
        ROLE_CORE: 3,
        ROLE_SHARED: 1,
        ROLE_CONTROL: 1,
        ROLE_PERIPHERAL: 1,
    }


def test_core_periphery_propagation_cost_by_hand():
    m = compute_architecture_metrics(_core_periphery_graph())
    # Visibility incl self:
    #   A->{A,B,C,L}=4, B->{B,C,A,L}=4, C->{C,A,B,L}=4,
    #   L->{L}=1, G->{G,A,B,C,L,P}=6, P->{P}=1. Total = 20.
    # Off-diagonal = 20 - 6 = 14; denominator = 6*5 = 30; pc = 14/30 = 0.4667.
    assert m.propagation_cost == round(14 / 30, 4)


# ---------------------------------------------------------------------------
# Structural-only guard: a co-change edge must not move any metric.
# ---------------------------------------------------------------------------


def test_cochange_edge_does_not_affect_metrics():
    base = _graph(["A", "B", "C"], [_edge("A", "B"), _edge("B", "C")])
    with_cochange = _graph(
        ["A", "B", "C"],
        [
            _edge("A", "B"),
            _edge("B", "C"),
            _edge("C", "A", kind="co_change", structural=False, match="inferred"),
        ],
    )
    a = compute_architecture_metrics(base)
    b = compute_architecture_metrics(with_cochange)
    assert a.to_dict() == b.to_dict()
    # The behavioral edge does not create a cyclic core.
    assert b.core_size == 0


# ---------------------------------------------------------------------------
# Determinism / snapshot: lock the shape and a known score.
# ---------------------------------------------------------------------------


def test_snapshot_shape_and_score_are_locked():
    m = compute_architecture_metrics(_core_periphery_graph(), generated_at="t0")
    payload = m.to_dict()

    assert set(payload.keys()) == {
        "node_count",
        "structural_edge_count",
        "propagation_cost",
        "propagation_cost_pct",
        "core_size",
        "core_ratio",
        "core_members",
        "cycle_count",
        "conformance_violations",
        "architecture_type",
        "score",
        "role_breakdown",
        "roles",
        "generated_at",
    }
    # score = 10 - 5*(14/30) - 3*(3/6) - min(2, 0.5*1) - 0
    #       = 10 - 2.3333 - 1.5 - 0.5 = 5.6667 -> 5.7
    assert payload["score"] == 5.7
    assert payload["node_count"] == 6
    assert payload["structural_edge_count"] == 6
    assert payload["generated_at"] == "t0"
    assert payload["roles"][0].keys() >= {"id", "visibility_fan_in", "role"}


def test_conformance_violations_lower_the_score():
    graph = _graph(["A", "B", "C"], [_edge("A", "B"), _edge("B", "C")])
    clean = compute_architecture_metrics(graph)
    flagged = compute_architecture_metrics(graph, conformance_violations=3)
    # 3 violations -> min(2, 0.5*3) = 1.5 point penalty.
    assert flagged.score == round(clean.score - 1.5, 1)
    assert flagged.conformance_violations == 3


# ---------------------------------------------------------------------------
# Degenerate workspaces.
# ---------------------------------------------------------------------------


def test_empty_workspace():
    m = compute_architecture_metrics(SystemGraph(nodes=[], edges=[]))
    assert m.node_count == 0
    assert m.propagation_cost == 0.0
    assert m.core_size == 0
    assert m.score == SCORE_MAX
    assert m.architecture_type == ARCH_HIERARCHICAL
    assert m.roles == []


def test_single_service_is_decoupled():
    m = compute_architecture_metrics(_graph(["solo"], []))
    assert m.node_count == 1
    assert m.propagation_cost == 0.0
    assert m.core_size == 0
    assert m.score == SCORE_MAX
    roles = _roles_by_id(m)
    assert roles["solo"].role == ROLE_PERIPHERAL


def test_independent_services_are_fully_decoupled():
    m = compute_architecture_metrics(_graph(["A", "B", "C"], []))
    assert m.propagation_cost == 0.0
    assert m.score == SCORE_MAX
    assert all(r.role == ROLE_PERIPHERAL for r in m.roles)


# ---------------------------------------------------------------------------
# Score helper bounds.
# ---------------------------------------------------------------------------


def test_score_is_clamped_to_range():
    assert architecture_score(1.0, 1.0, 100, 100) >= 1.0
    assert architecture_score(0.0, 0.0, 0, 0) == SCORE_MAX
