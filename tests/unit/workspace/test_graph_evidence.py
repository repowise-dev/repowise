"""The system graph's evidence state, and the views that must honour it."""

from __future__ import annotations

from repowise.core.workspace.blast_radius import cross_repo_blast_radius
from repowise.core.workspace.conformance import build_conformance_report
from repowise.core.workspace.diagnostics import ExtractionDiagnostics
from repowise.core.workspace.graph_evidence import (
    _RULES,
    EVIDENCE_INSUFFICIENT,
    EVIDENCE_MEASURED,
    EVIDENCE_PARTIAL,
    graph_evidence,
)
from repowise.core.workspace.system_graph import SystemEdge, SystemGraph, SystemNode


def _edge(source: str, target: str, kind: str = "http", structural: bool = True) -> SystemEdge:
    return SystemEdge(
        id=f"{source}->{target}:{kind}",
        source=source,
        target=target,
        kind=kind,
        match_type="exact",
        confidence=1.0,
        weight=1,
        structural=structural,
    )


def _graph(nodes: list[str], edges: list[SystemEdge], walked: list[str] | None = None):
    return SystemGraph(
        nodes=[SystemNode(id=n, repo=n, service_path=None, name=n) for n in nodes],
        edges=edges,
        diagnostics=ExtractionDiagnostics(total_providers=55, total_consumers=7),
        repo_provenance={r: {} for r in (walked if walked is not None else nodes)},
    )


def _codes(evidence) -> list[str]:
    return [r.code for r in evidence.reasons]


def test_providers_without_any_link_are_insufficient():
    """The Sock Shop shape: 55 providers, 7 consumers, 0 edges."""
    evidence = graph_evidence(_graph(["carts", "orders", "front-end"], []))
    assert evidence.state == EVIDENCE_INSUFFICIENT
    assert not evidence.determined
    assert _codes(evidence) == ["no_cross_service_edges"]
    assert "55 provider(s) and 7 consumer(s)" in evidence.summary


def test_one_service_is_insufficient():
    evidence = graph_evidence(_graph(["solo"], [], walked=["solo", "other"]))
    assert evidence.state == EVIDENCE_INSUFFICIENT
    assert _codes(evidence) == ["too_few_services", "repos_without_contracts"]


def test_build_references_only_are_partial():
    """The eShop shape: project references and no runtime edge."""
    evidence = graph_evidence(
        _graph(["a", "b", "c"], [_edge("a", "b", "package"), _edge("b", "c", "package")])
    )
    assert evidence.state == EVIDENCE_PARTIAL
    assert evidence.determined
    assert _codes(evidence) == ["build_edges_only"]
    assert evidence.build_edges == 2
    assert evidence.runtime_edges == 0


def test_an_isolated_service_and_a_repo_without_contracts_are_partial():
    graph = _graph(
        ["web", "api", "batch"], [_edge("web", "api")], walked=["web", "api", "batch", "infra"]
    )
    evidence = graph_evidence(graph)
    assert evidence.state == EVIDENCE_PARTIAL
    assert _codes(evidence) == ["isolated_services", "repos_without_contracts"]
    assert evidence.reasons[0].subjects == ["batch"]
    assert evidence.reasons[1].subjects == ["infra"]


def test_a_co_change_edge_connects_nothing():
    graph = _graph(["a", "b"], [_edge("a", "b", "co_change", structural=False)])
    assert graph_evidence(graph).state == EVIDENCE_INSUFFICIENT


def test_every_service_on_a_runtime_edge_is_measured():
    graph = _graph(["a", "b", "c"], [_edge("a", "b"), _edge("c", "b", "event")])
    evidence = graph_evidence(graph)
    assert evidence.state == EVIDENCE_MEASURED
    assert evidence.reasons == []
    assert evidence.summary == ""
    assert evidence.to_dict()["determined"] is True


def test_every_rule_reports_a_closed_state():
    """A rule's reason names the state it implies, from the closed vocabulary."""
    worst = _graph(["x"], [], walked=["x", "y"])
    graphs = [
        worst,
        _graph(["a", "b"], []),
        _graph(["a", "b"], [_edge("a", "b", "package")]),
        _graph(["a", "b", "c"], [_edge("a", "b")], walked=["a", "b", "c", "d"]),
    ]
    seen = {r.code: r.state for g in graphs for r in graph_evidence(g).reasons}
    assert len(seen) == len(_RULES)
    assert set(seen.values()) <= {EVIDENCE_PARTIAL, EVIDENCE_INSUFFICIENT}


def test_conformance_report_carries_the_evidence():
    report = build_conformance_report(_graph(["a", "b"], []), [])
    assert report.cycles == []
    assert report.cycles_determined is False
    assert report.to_dict()["evidence"]["state"] == EVIDENCE_INSUFFICIENT

    measured = build_conformance_report(_graph(["a", "b"], [_edge("a", "b")]), [])
    assert measured.cycles_determined is True


def test_blast_radius_names_targets_it_cannot_judge():
    graph = _graph(["web", "api", "batch"], [_edge("web", "api")])
    result = cross_repo_blast_radius(graph, ["api", "batch"])
    assert result.undetermined_targets == ["batch"]
    assert [n.id for n in result.impacted] == ["web"]
    assert result.to_dict()["evidence"]["state"] == EVIDENCE_PARTIAL

    empty = cross_repo_blast_radius(_graph(["a", "b"], []), ["a"])
    assert empty.total_impacted == 0
    assert empty.undetermined_targets == ["a"]
    assert empty.evidence["determined"] is False


def test_a_co_change_partner_is_determined_when_the_walk_follows_co_change():
    graph = _graph(["a", "b"], [_edge("a", "b", "co_change", structural=False)])
    walked = cross_repo_blast_radius(graph, ["a"])
    assert walked.undetermined_targets == []
    assert [n.id for n in walked.impacted] == ["b"]
    assert cross_repo_blast_radius(graph, ["a"], include_behavioral=False).undetermined_targets == [
        "a"
    ]
