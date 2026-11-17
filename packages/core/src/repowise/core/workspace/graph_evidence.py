"""How much evidence the system graph rests on, stated once for every view.

A graph with no cross-service edges scores as perfectly decoupled, has no
cycles and no blast radius, which reads as a clean result when it means
nothing was linked. :func:`graph_evidence` names that state once, from the
graph alone, so the metrics, ``workspace check``, blast radius, test impact,
diagnostics, MCP and REST all say the same thing about the same artifact:

* ``insufficient``: fewer than two services carry a contract, or no structural
  edge joins any two. Scores, cycle verdicts and impact sets are not determined.
* ``partial``: edges exist, but part of the estate is out of sight (only build
  references, services with no detected dependency, repos that yielded no
  contracts). Figures are computed and carry the reasons beside them.
* ``measured``: every service takes part in a runtime dependency.

Pure and I/O-free. Each rule is one entry in :data:`_RULES`, so a new kind of
missing evidence is one function and one registry line.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from repowise.core.workspace.system_graph import SystemGraph

EVIDENCE_MEASURED = "measured"
EVIDENCE_PARTIAL = "partial"
EVIDENCE_INSUFFICIENT = "insufficient"

#: Cap on the names a reason lists; its count is always the true one.
_MAX_NAMED = 8


@dataclass(frozen=True)
class EvidenceReason:
    """One reason the evidence is short of measured, with the subjects it names."""

    code: str
    state: str  # the state this reason alone implies: partial | insufficient
    message: str
    subjects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "state": self.state,
            "message": self.message,
            "subjects": self.subjects,
        }


@dataclass(frozen=True)
class GraphEvidence:
    """The evidence state of one system graph, plus the counts behind it."""

    state: str
    reasons: list[EvidenceReason]
    services: int
    runtime_edges: int
    build_edges: int
    providers: int
    consumers: int

    @property
    def determined(self) -> bool:
        """False when a score, cycle verdict or impact set would be vacuous."""
        return self.state != EVIDENCE_INSUFFICIENT

    @property
    def summary(self) -> str:
        """The reasons as one sentence, empty when measured."""
        return "; ".join(r.message for r in self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "determined": self.determined,
            "summary": self.summary,
            "reasons": [r.to_dict() for r in self.reasons],
            "services": self.services,
            "runtime_edges": self.runtime_edges,
            "build_edges": self.build_edges,
            "providers": self.providers,
            "consumers": self.consumers,
        }


@dataclass(frozen=True)
class _Facts:
    """What every rule reads, computed once per graph."""

    services: list[str]
    runtime_edges: int
    build_edges: int
    providers: int
    consumers: int
    connected: set[str]
    walked_repos: list[str]
    node_repos: set[str]


def _named(subjects: list[str]) -> str:
    shown = ", ".join(subjects[:_MAX_NAMED])
    extra = len(subjects) - _MAX_NAMED
    return f"{shown}, +{extra} more" if extra > 0 else shown


def _too_few_services(facts: _Facts) -> EvidenceReason | None:
    if len(facts.services) >= 2:
        return None
    count = len(facts.services)
    return EvidenceReason(
        code="too_few_services",
        state=EVIDENCE_INSUFFICIENT,
        message=(
            "no service carries a contract"
            if count == 0
            else "only one service carries a contract, so there is nothing to relate it to"
        ),
        subjects=list(facts.services),
    )


def _no_cross_service_edges(facts: _Facts) -> EvidenceReason | None:
    if len(facts.services) < 2 or facts.runtime_edges or facts.build_edges:
        return None
    return EvidenceReason(
        code="no_cross_service_edges",
        state=EVIDENCE_INSUFFICIENT,
        message=(
            f"no dependency between services was detected ({facts.providers} provider(s) "
            f"and {facts.consumers} consumer(s) located, none linked across services)"
        ),
    )


def _build_edges_only(facts: _Facts) -> EvidenceReason | None:
    if facts.runtime_edges or not facts.build_edges:
        return None
    return EvidenceReason(
        code="build_edges_only",
        state=EVIDENCE_PARTIAL,
        message=(
            f"every detected dependency is a build reference ({facts.build_edges} package "
            "or project edge(s)); no runtime call, event or shared table was linked"
        ),
    )


def _isolated_services(facts: _Facts) -> EvidenceReason | None:
    if not facts.connected:
        return None  # nothing is connected: the edge rules above own that case
    isolated = [s for s in facts.services if s not in facts.connected]
    if not isolated:
        return None
    return EvidenceReason(
        code="isolated_services",
        state=EVIDENCE_PARTIAL,
        message=(
            f"{len(isolated)} of {len(facts.services)} service(s) have no detected "
            f"dependency in either direction: {_named(isolated)}"
        ),
        subjects=isolated,
    )


def _repos_without_contracts(facts: _Facts) -> EvidenceReason | None:
    missing = [r for r in facts.walked_repos if r not in facts.node_repos]
    if not missing:
        return None
    return EvidenceReason(
        code="repos_without_contracts",
        state=EVIDENCE_PARTIAL,
        message=(
            f"{len(missing)} repo(s) were read but yielded no contracts, so they are "
            f"not in the graph: {_named(missing)}"
        ),
        subjects=missing,
    )


#: Every rule, in reporting order. A rule returns a reason or None.
_RULES: tuple[Callable[[_Facts], EvidenceReason | None], ...] = (
    _too_few_services,
    _no_cross_service_edges,
    _build_edges_only,
    _isolated_services,
    _repos_without_contracts,
)


def _facts(graph: SystemGraph) -> _Facts:
    services = sorted(n.id for n in graph.nodes)
    known = set(services)
    runtime = build = 0
    connected: set[str] = set()
    for edge in graph.edges:
        if not edge.structural or edge.source not in known or edge.target not in known:
            continue
        if edge.family == "build":  # a manifest or package reference, not a call
            build += 1
        else:
            runtime += 1
        connected.update((edge.source, edge.target))
    diagnostics = graph.diagnostics
    return _Facts(
        services=services,
        runtime_edges=runtime,
        build_edges=build,
        providers=diagnostics.total_providers,
        consumers=diagnostics.total_consumers,
        connected=connected,
        walked_repos=sorted(graph.repo_provenance),
        node_repos={n.repo for n in graph.nodes},
    )


def graph_evidence(graph: SystemGraph) -> GraphEvidence:
    """The evidence state of *graph*: measured, partial or insufficient, with reasons.

    The state is the worst any rule implies. Pure; linear in nodes and edges.
    """
    facts = _facts(graph)
    reasons = [r for rule in _RULES if (r := rule(facts)) is not None]
    if any(r.state == EVIDENCE_INSUFFICIENT for r in reasons):
        state = EVIDENCE_INSUFFICIENT
    elif reasons:
        state = EVIDENCE_PARTIAL
    else:
        state = EVIDENCE_MEASURED
    return GraphEvidence(
        state=state,
        reasons=reasons,
        services=len(facts.services),
        runtime_edges=facts.runtime_edges,
        build_edges=facts.build_edges,
        providers=facts.providers,
        consumers=facts.consumers,
    )
