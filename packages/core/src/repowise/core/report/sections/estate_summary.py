"""The estate at a glance: its repositories, its services and the evidence behind them."""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import (
    Section,
    WorkspaceReportContext,
    capped,
    capped_names,
    check,
    report_sections,
)
from repowise.core.report.sections._estate import missing_graph

_METHOD = (
    "the workspace config, each member's index, and the system graph and contract "
    "extraction 'repowise update --workspace' stored"
)


def _repositories(ctx: WorkspaceReportContext, graph: Any) -> dict[str, Any]:
    walked = graph.repo_provenance
    services: dict[str, list[str]] = {}
    for node in graph.nodes:
        services.setdefault(node.repo, []).append(node.name)
    rows = []
    for entry in ctx.repos:
        alias = entry.get("alias") or entry.get("name")
        indexed = entry.get("indexed", True)
        extracted = (walked.get(alias) or {}).get("head")
        head = entry.get("head_commit")
        rows.append(
            {
                "alias": alias,
                "indexed": indexed,
                "indexed_commit": head,
                "contracts_commit": extracted,
                "services": sorted(services.get(alias, [])),
                "index_state": entry.get("index_state"),
            }
        )
    not_indexed = [r["alias"] for r in rows if not r["indexed"]]
    behind_index = [r["alias"] for r in rows if r["index_state"] == "behind"]
    unopened = [r["alias"] for r in rows if r["index_state"] == "unreadable"]
    unread = [
        r["alias"] for r in rows
        if r["indexed"] and not r["index_state"] and r["alias"] not in walked
    ]
    empty = [r["alias"] for r in rows if r["alias"] in walked and not r["services"]]
    behind = [
        r
        for r in rows
        if r["indexed"] and r["contracts_commit"] and r["indexed_commit"]
        and r["contracts_commit"] != r["indexed_commit"]
    ]
    details = []
    if not_indexed:
        details.append(f"not indexed, so nothing about them is known: {capped_names(not_indexed)}")
    if behind_index:
        details.append(
            "index written by an older repowise, so its commit was not read; run "
            f"'repowise update' there: {capped_names(behind_index)}"
        )
    if unopened:
        details.append(f"index could not be opened or holds no repository: {capped_names(unopened)}")
    if unread:
        details.append(f"indexed but not read by the last workspace update: {capped_names(unread)}")
    if empty:
        details.append(f"read but yielded no contracts, so not in the graph: {capped_names(empty)}")
    details += capped(
        [
            f"{r['alias']}: contracts read at {r['contracts_commit'][:12]}, index at "
            f"{r['indexed_commit'][:12]}"
            for r in behind
        ]
    )
    in_graph = sum(1 for r in rows if r["services"])
    return check(
        "repositories",
        reason=(
            f"{len(rows)} configured: {len(rows) - len(not_indexed)} indexed, "
            f"{sum(1 for r in rows if r['alias'] in walked)} read "
            f"by contract extraction, {in_graph} with a service in the system graph"
            + (f"; {len(behind)} with contracts read at another commit than the index" if behind else "")
        ),
        details=details,
        method=_METHOD,
        basis="measured",
        data={"repositories": rows},
    )


def _services(graph: Any) -> dict[str, Any]:
    by_repo: dict[str, list[str]] = {}
    for node in graph.nodes:
        by_repo.setdefault(node.repo, []).append(f"{node.name} ({node.kind})")
    isolated = sorted(n.id for n in graph.nodes if n.is_isolated)
    providing = sum(1 for n in graph.nodes if n.provider_count)
    consuming = sum(1 for n in graph.nodes if n.consumer_count)
    details = capped([f"{repo}: {', '.join(sorted(names))}" for repo, names in sorted(by_repo.items())])
    if isolated:
        details.append(f"no detected dependency either way: {capped_names(isolated)}")
    return check(
        "services",
        reason=(
            f"{len(graph.nodes)} service(s) across {len(by_repo)} repositories: {providing} "
            f"provide a contract, {consuming} consume one, {len(isolated)} isolated"
        ),
        details=details,
        method=(
            "service boundaries from build manifests (a repo without one is one service), "
            "kept when a contract was read from them"
        ),
        basis="inferred",
        data={"services": [n.to_dict() for n in graph.nodes]},
    )


def _evidence(ctx: WorkspaceReportContext, graph: Any) -> dict[str, Any]:
    evidence = ctx.evidence()
    counts = (
        f"{evidence.runtime_edges} runtime and {evidence.build_edges} build edge(s) across "
        f"{evidence.services} service(s)"
    )
    if evidence.state == "measured":
        reason = f"measured: every service takes part in a runtime dependency ({counts})"
    elif evidence.state == "partial":
        reason = f"partial: figures are computed, with part of the estate out of sight ({counts})"
    else:
        reason = (
            f"insufficient: architecture figures, cycle verdicts and impact sets are not "
            f"determined ({counts})"
        )
    return check(
        "evidence",
        reason=reason,
        details=[
            *(r.message for r in evidence.reasons),
            f"system graph written {graph.generated_at or 'at an unknown time'}",
        ],
        method="the system graph's evidence rules: services, cross-service edges, isolated "
        "services and repos that yielded no contracts",
        basis="unresolved" if evidence.state == "insufficient" else "measured",
        data=evidence.to_dict(),
    )


async def collect(ctx: WorkspaceReportContext) -> list[dict[str, Any]]:
    graph = ctx.system_graph()
    if graph is None:
        return missing_graph("estate", _METHOD)
    return [_repositories(ctx, graph), _services(graph), _evidence(ctx, graph)]


report_sections.register(
    "estate-summary",
    Section(
        title="Estate summary",
        summary="Repositories, services and how much evidence the system graph rests on.",
        collect=collect,
        order=10,
        report="estate",
        scope="workspace",
    ),
)
