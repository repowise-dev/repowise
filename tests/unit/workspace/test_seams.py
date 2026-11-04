"""Riskiest seams: the fixed heuristic over the system graph, its inputs kept per pair."""

from __future__ import annotations

from repowise.core.workspace.seams import rank_seams, service_reach
from repowise.core.workspace.system_graph import SystemEdge, SystemGraph, SystemNode


def _node(repo: str) -> SystemNode:
    return SystemNode(id=repo, repo=repo, service_path=None, name=repo)


def _edge(src: str, dst: str, kind: str = "http", confidence: float = 0.8) -> SystemEdge:
    return SystemEdge(
        id=f"{src}->{dst}:{kind}",
        source=src,
        target=dst,
        kind=kind,
        match_type="inferred" if kind == "co_change" else "exact",
        confidence=confidence,
        weight=1,
        structural=kind != "co_change",
    )


def _graph(*edges: SystemEdge) -> SystemGraph:
    repos = sorted({r for e in edges for r in (e.source, e.target)})
    return SystemGraph(nodes=[_node(r) for r in repos], edges=list(edges))


HISTORY = {"web": 40, "api": 40, "db": 40, "lib": 40, "ops": 40}


def test_reach_counts_every_service_a_change_reaches_structurally():
    graph = _graph(_edge("web", "api"), _edge("api", "db"), _edge("ops", "db", "co_change", 0.9))
    assert service_reach(graph) == {"api": 1, "db": 2, "ops": 0, "web": 0}


def test_score_is_confidence_times_reach_times_undeclared_co_change():
    graph = _graph(
        _edge("web", "api"),  # contract, co-changes: declared, so co-change adds nothing
        _edge("web", "api", "co_change", 0.5),
        _edge("api", "lib", "package", 1.0),  # no contract, co-changes: undeclared
        _edge("api", "lib", "co_change", 0.4),
        _edge("ops", "db", "co_change", 0.6),  # co-change alone
    )
    by_pair = {s.services: s for s in rank_seams(graph, HISTORY)}
    lib = by_pair[("api", "lib")]
    assert (lib.confidence, lib.reach, lib.undeclared, lib.score) == (1.0, 2, 0.4, 2.8)
    api = by_pair[("web", "api")]
    assert api.declared and api.co_change == 0.5 and api.undeclared == 0.0
    assert api.score == 0.8  # 0.8 x reach 1 x (1 + 0)
    ops = by_pair[("db", "ops")]
    assert (ops.direction, ops.confidence, ops.score) == ("~", None, 0.6)
    assert rank_seams(graph, HISTORY)[0].services == ("api", "lib")


def test_short_history_is_shown_as_not_measured_not_scored_as_zero():
    graph = _graph(_edge("web", "api", "package", 1.0), _edge("api", "db"), _edge("ops", "db"))
    commits = {"web": 1, "api": 30, "db": 30, "ops": 1}
    seams = {s.services: s for s in rank_seams(graph, commits)}
    short = seams[("web", "api")]  # no contract: co-change would count, and is unknown
    assert short.co_change is None and short.undeclared is None
    assert short.missing == ["co-change not measured: web has 1 commit indexed"]
    assert short.score == 1.0 and short.score_is_floor  # the missing factor can only raise it
    declared = seams[("ops", "db")]  # a contract: co-change cannot change its score
    assert declared.undeclared == 0.0 and not declared.missing and not declared.score_is_floor
    assert seams[("api", "db")].co_change is None and not seams[("api", "db")].missing
    unknown = rank_seams(graph, None)
    assert all("no indexed history" in m for s in unknown for m in s.missing)


def test_both_ways_takes_the_wider_reach():
    graph = _graph(_edge("web", "api"), _edge("api", "web", "event"), _edge("ops", "api"))
    (pair,) = [s for s in rank_seams(graph, HISTORY) if set(s.services) == {"web", "api"}]
    assert pair.direction == "<->" and pair.kinds == ["event", "http"]
    assert pair.reach == 2  # a change to api reaches web and ops
    assert pair.to_dict()["undeclared_co_change"] == 0.0
