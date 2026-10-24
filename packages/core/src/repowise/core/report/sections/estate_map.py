"""The system map and its architecture figures, each with the evidence it rests on."""

from __future__ import annotations

from collections import Counter
from typing import Any

from repowise.core.report.model import (
    Section,
    WorkspaceReportContext,
    capped,
    check,
    report_sections,
)
from repowise.core.report.sections._estate import evidence_note, missing_graph

_EDGES = (
    "the stored system graph: contract links folded into service-to-service edges, with "
    "package dependencies and co-change; an edge keeps the strategy and confidence of its "
    "strongest link"
)
_ARCHITECTURE = (
    "architecture metrics over the system graph's structural edges (propagation cost, cyclic "
    "core, cycles, declared-rule violations), as 'repowise workspace metrics' computes them; "
    "the 1-10 score is a fixed formula, not a calibrated grade"
)


def _dependencies(ctx: WorkspaceReportContext, graph: Any) -> dict[str, Any]:
    structural = [e for e in graph.edges if e.structural]
    co_change = len(graph.edges) - len(structural)
    by_kind = Counter(e.kind for e in structural)
    kinds = ", ".join(f"{kind} {n}" for kind, n in sorted(by_kind.items())) or "none"
    ranked = sorted(structural, key=lambda e: (-e.weight, e.source, e.target, e.kind))
    return check(
        "dependencies",
        reason=(
            f"{len(structural)} service dependency edge(s) ({kinds}), plus {co_change} "
            f"co-change edge(s) that record history, not a dependency"
            + evidence_note(ctx.evidence())
        ),
        details=capped(
            [
                f"{e.source} -> {e.target}: {e.kind}, {e.weight} link(s), "
                f"{e.strategy or e.match_type}, confidence {e.confidence:.2f}"
                for e in ranked
            ]
        ),
        method=_EDGES,
        basis="inferred",
        data={"edges": [e.to_dict() for e in graph.edges]},
    )


def _architecture(ctx: WorkspaceReportContext, graph: Any) -> dict[str, Any]:
    from repowise.core.workspace.architecture_metrics import compute_architecture_metrics
    from repowise.core.workspace.conformance import check_conformance, tags_by_repo_from_config
    from repowise.core.workspace.cycles import detect_cycles

    config = ctx.config()
    violations = check_conformance(
        graph, config.conformance.rules, tags_by_repo_from_config(config)
    )
    metrics = compute_architecture_metrics(
        graph, conformance_violations=len(violations), generated_at=graph.generated_at
    )
    data = metrics.to_dict()
    if metrics.score is None:
        return check(
            "architecture",
            reason=f"not determined: {ctx.evidence().summary}",
            method=_ARCHITECTURE,
            basis="unresolved",
            data=data,
        )
    cycles, _total = detect_cycles(graph)
    roles = metrics.role_breakdown()
    details = [
        f"propagation cost {metrics.propagation_cost_pct:.1f}%: the average share of other "
        "services one service can reach",
        f"cyclic core: {', '.join(metrics.core_members)}" if metrics.core_members
        else "cyclic core: none",
        f"{metrics.cycle_count} dependency cycle(s)",
        *capped(
            [
                " -> ".join([*c.nodes, c.nodes[0]])
                + (f" ({c.async_legs} event leg(s))" if c.async_legs else "")
                for c in cycles
            ],
            limit=10,
        ),
        "service roles: " + ", ".join(f"{role} {n}" for role, n in sorted(roles.items())),
    ]
    if metrics.conformance_violations:
        details.append(f"{metrics.conformance_violations} declared-rule violation(s)")
    return check(
        "architecture",
        reason=f"{metrics.architecture_type}, scored {metrics.score:g} of 10"
        + evidence_note(ctx.evidence()),
        score=metrics.score,
        details=details,
        method=_ARCHITECTURE,
        basis="inferred",
        data=data,
    )


async def collect(ctx: WorkspaceReportContext) -> list[dict[str, Any]]:
    graph = ctx.system_graph()
    if graph is None:
        return missing_graph("system-map", _EDGES)
    return [_dependencies(ctx, graph), _architecture(ctx, graph)]


report_sections.register(
    "estate-map",
    Section(
        title="System map and architecture",
        summary="How the services depend on each other, and what that structure scores.",
        collect=collect,
        order=20,
        report="estate",
        scope="workspace",
    ),
)
