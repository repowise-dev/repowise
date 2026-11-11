"""``repowise workspace metrics``: architecture-complexity metrics."""

from __future__ import annotations

import click

from repowise.cli.helpers import console, resolve_repo_path
from repowise.cli.output import emit_json, format_option, json_option, resolve_format

from ._shared import _print_evidence, _report_artifact_provenance, _require_workspace
from .group import workspace_group


@workspace_group.command("metrics")
@click.argument("path", required=False, default=None)
@format_option(help="Output format. ``json`` emits the raw metrics.")
@json_option()
def workspace_metrics(path: str | None, fmt: str, as_json: bool) -> None:
    """Architecture metrics — propagation cost, core, and a 1-10 score.

    Computes the standard architecture-complexity metrics over the system graph
    built by 'repowise update --workspace': how coupled the whole system is
    (propagation cost), which services form the cyclic core, and a single
    deterministic 1-10 score. Uses structural edges only; co-change is excluded.
    Declared-rule violations, if any, are folded into the score. CI-friendly
    plain output.
    """
    from repowise.core.workspace.architecture_metrics import compute_architecture_metrics
    from repowise.core.workspace.conformance import (
        check_conformance,
        tags_by_repo_from_config,
    )
    from repowise.core.workspace.cycles import cycle_total
    from repowise.core.workspace.system_graph import load_system_graph

    fmt =resolve_format(fmt, as_json)
    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    graph = load_system_graph(ws_root)
    if graph is None:
        raise click.ClickException(
            "No system graph found. Run 'repowise update --workspace' to build "
            "cross-repo relationships first."
        )

    violations = check_conformance(
        graph, ws_config.conformance.rules, tags_by_repo_from_config(ws_config)
    )
    metrics = compute_architecture_metrics(
        graph,
        conformance_violations=len(violations),
        generated_at=graph.generated_at,
    )

    if fmt == "json":
        emit_json(metrics.to_dict())
        return

    if metrics.node_count == 0:
        console.print(
            "[dim]No services in the system graph yet.[/dim] Run "
            "'repowise update --workspace' after indexing repos with cross-repo "
            "relationships."
        )
        return

    _report_artifact_provenance(
        ws_root,
        ws_config,
        graph.generated_at,
        sorted({n.repo for n in graph.nodes}),
        graph.repo_provenance,
    )
    evidence = metrics.evidence.to_dict() if metrics.evidence is not None else None
    _print_evidence(evidence)

    if metrics.score is None:
        # Nothing was seen to relate: no score, coupling or role is claimed.
        console.print(
            f"\n  Architecture score  [bold]not determined[/bold]   [dim]({metrics.node_count} "
            f"service(s), {metrics.structural_edge_count} structural edge(s))[/dim]"
        )
        return

    qualifier = "" if evidence is None or evidence["state"] == "measured" else ", partial evidence"
    score_color = "green" if metrics.score >= 8 else "yellow" if metrics.score >= 4 else "red"
    console.print(
        f"\n  Architecture score  [bold {score_color}]{metrics.score:.1f}[/bold {score_color}]"
        f" / 10   [dim]({metrics.architecture_type}{qualifier})[/dim]"
    )
    console.print(
        f"  Propagation cost    [bold]{metrics.propagation_cost_pct:.1f}%[/bold]"
        f"   [dim]avg share of other services each one can reach[/dim]"
    )
    if metrics.core_size:
        members = ", ".join(metrics.core_members[:6])
        if len(metrics.core_members) > 6:
            members += f", +{len(metrics.core_members) - 6} more"
        console.print(
            f"  Cyclic core         [bold]{metrics.core_size}[/bold] service(s)"
            f" ([dim]{metrics.core_ratio * 100:.0f}% of {metrics.node_count}[/dim]) — {members}"
        )
    else:
        console.print(
            f"  Cyclic core         [green]none[/green]"
            f"   [dim]({metrics.node_count} services, acyclic structure)[/dim]"
        )
    console.print(
        "  Dependency cycles   [bold]"
        f"{cycle_total(metrics.cycle_count or 0, metrics.cycle_count_capped)}[/bold]"
    )
    if metrics.conformance_violations:
        console.print(
            f"  Rule violations     [red]{metrics.conformance_violations}[/red]"
            f"   [dim](folded into the score)[/dim]"
        )

    breakdown = metrics.role_breakdown()
    role_labels = {
        "core": "Core",
        "shared": "Shared",
        "control": "Control",
        "peripheral": "Peripheral",
    }
    parts = ", ".join(f"{role_labels[r]} {breakdown.get(r, 0)}" for r in role_labels)
    console.print(f"\n  Service roles: {parts}")
