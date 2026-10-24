"""``repowise workspace check``: architecture lint for CI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.markup import escape

from repowise.cli.helpers import console, resolve_repo_path
from repowise.cli.output import emit_json, format_option, json_option, resolve_format

from ._shared import _print_evidence, _report_artifact_provenance, _require_workspace
from .group import workspace_group

if TYPE_CHECKING:
    from repowise.core.workspace.breaking_change import BreakingChange, BreakingChangeReport
    from repowise.core.workspace.config import WorkspaceConfig
    from repowise.core.workspace.conformance import ConformanceReport
    from repowise.core.workspace.cycles import DependencyCycle
    from repowise.core.workspace.system_graph import SystemGraph


@workspace_group.command("check")
@click.argument("path", required=False, default=None)
@click.option(
    "--breaking/--no-breaking",
    default=True,
    help="Fail on breaking contract changes from the last workspace update.",
)
@format_option(help="Output format. ``json`` emits the raw conformance report.")
@json_option()
def workspace_check(path: str | None, breaking: bool, fmt: str, as_json: bool) -> None:
    """Architecture lint — fail on rule violations, cycles, or broken contracts.

    Checks the declared ``conformance`` rules in ``.repowise-workspace.yaml``
    against the system graph and detects circular service dependencies. Exits
    non-zero when any violation or cycle is found, so it can gate CI. Reads (and
    recomputes from) the system graph built by 'repowise update --workspace', so
    editing rules and re-running picks them up without a full re-index.

    Also fails on the breaking contract changes the last workspace update
    detected — a removed endpoint or a retyped field a consumer in another repo
    still calls. Unlike the rules above these need nothing declared, so
    ``--no-breaking`` is there for a pipeline that wants only its own rules
    enforced. They are read from that update's report, not recomputed here.
    """
    import sys

    from repowise.core.workspace.conformance import (
        build_conformance_report,
        tags_by_repo_from_config,
    )
    from repowise.core.workspace.system_graph import load_system_graph

    fmt = resolve_format(fmt, as_json)
    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    graph = load_system_graph(ws_root)
    if graph is None:
        raise click.ClickException(
            "No system graph found. Run 'repowise update --workspace' to build "
            "cross-repo relationships first."
        )

    report = build_conformance_report(
        graph,
        ws_config.conformance.rules,
        tags_by_repo_from_config(ws_config),
    )

    # Gate on wire-incompatible changes that endanger another repo. A warning
    # severity is source-compat only and must not fail a build.
    bc_report = _load_breaking_report(ws_root) if breaking else None
    bc_ran = bc_report is not None and bc_report.ran
    breaking_changes = _cross_repo_breaking_changes(bc_report) if bc_ran else []

    if fmt == "json":
        _emit_check_json(report, breaking, bc_report, bc_ran, breaking_changes)
        if report.has_findings or breaking_changes:
            sys.exit(1)
        return

    _print_check_scope(ws_root, ws_config, graph, report)
    _print_violations(report)
    _print_cycles(report)
    _print_breaking_changes(breaking_changes, bc_report)

    if not report.has_findings and not breaking_changes:
        _print_check_pass(report, bc_ran)
        return

    # Only claim a breaking-change count when a detection pass produced one.
    tail = f", {len(breaking_changes)} breaking contract change(s)" if bc_ran else ""
    console.print(
        f"\n[red]Architecture check failed:[/red] {len(report.violations)} violation(s), "
        f"{len(report.cycles)} cycle(s){tail}."
    )
    sys.exit(1)


def _load_breaking_report(ws_root: Path) -> BreakingChangeReport | None:
    from repowise.core.workspace.breaking_change import load_breaking_change_report

    return load_breaking_change_report(ws_root)


def _cross_repo_breaking_changes(bc_report: BreakingChangeReport) -> list[BreakingChange]:
    """Breaking-severity changes with a consumer outside the provider's repo."""
    from repowise.core.workspace.breaking_change import SEVERITY_BREAKING

    return [
        c
        for c in bc_report.changes
        if c.severity == SEVERITY_BREAKING
        and any(ic.repo != c.provider_repo for ic in c.impacted_consumers)
    ]


def _emit_check_json(
    report: ConformanceReport,
    breaking: bool,
    bc_report: BreakingChangeReport | None,
    bc_ran: bool,
    breaking_changes: list[BreakingChange],
) -> None:
    payload = report.to_dict()
    if breaking:
        payload["breaking_changes"] = [c.to_dict() for c in breaking_changes]
        # The conformance half is recomputed now; this half is an artifact
        # of arbitrary age, so it carries its own stamp.
        payload["breaking_changes_generated_at"] = (
            bc_report.generated_at if bc_report is not None else None
        )
        # False = never detected, so the empty list above is not a pass.
        payload["breaking_changes_available"] = bc_ran
    emit_json(payload)


def _print_check_scope(
    ws_root: Path,
    ws_config: WorkspaceConfig,
    graph: SystemGraph,
    report: ConformanceReport,
) -> None:
    """State the scope before the verdict.

    "No cycles" over an empty graph and "no cycles" over 7 services across 3
    repos are the same sentence and very different results, and only one of
    them is a pass.
    """
    _report_artifact_provenance(
        ws_root,
        ws_config,
        graph.generated_at,
        sorted({n.repo for n in graph.nodes}),
        graph.repo_provenance,
    )
    _print_evidence(report.evidence)
    console.print(
        f"Checked [bold]{len(graph.nodes)}[/bold] service(s) across "
        f"[bold]{len({n.repo for n in graph.nodes})}[/bold] repo(s) against "
        f"[bold]{report.rules_evaluated}[/bold] declared rule(s)."
    )
    if report.rules_evaluated == 0:
        console.print(
            "[dim]No conformance rules declared.[/dim] Add a [bold]conformance:[/bold] "
            "block to .repowise-workspace.yaml to enforce allowed dependencies."
        )


def _print_violations(report: ConformanceReport) -> None:
    if not report.violations:
        return
    console.print(f"\n[red]✗ {len(report.violations)} architecture rule violation(s):[/red]")
    for v in report.violations:
        rule = f"{v.rule_source} !-> {v.rule_target}"
        console.print(
            f"  [red]{v.source}[/red] -> [red]{v.target}[/red] "
            f"([dim]{v.edge_kind}[/dim]) violates [yellow]{rule}[/yellow]"
        )
        if v.rule_description:
            console.print(f"      [dim]{v.rule_description}[/dim]")


def _print_cycles(report: ConformanceReport) -> None:
    if not report.cycles:
        return
    console.print(f"\n[red]✗ {len(report.cycles)} dependency cycle(s):[/red]")
    console.print(
        "  [dim]a -\\[kind]-> b: a depends on b (a subscriber depends on its publisher).[/dim]"
    )
    for c in report.cycles:
        console.print(f"  [red]{escape(_cycle_path(c))}[/red]")
        if c.async_legs:
            console.print(
                f"      [dim]{c.async_legs} of {len(c.leg_kinds)} leg(s) only an event "
                "joins; the loop still couples schemas and deploy order.[/dim]"
            )


def _cycle_path(cycle: DependencyCycle) -> str:
    """``a -[http]-> b -[event]-> a``, each leg labelled with its edge kinds."""
    if not cycle.nodes:
        return ""
    parts = [cycle.nodes[0]]
    for i, kinds in enumerate(cycle.leg_kinds):
        nxt = cycle.nodes[(i + 1) % len(cycle.nodes)]
        parts.append(f" -[{'/'.join(kinds) or '?'}]-> {nxt}")
    return "".join(parts)


def _print_breaking_changes(
    breaking_changes: list[BreakingChange], bc_report: BreakingChangeReport | None
) -> None:
    if not breaking_changes:
        return
    from rich.markup import escape

    stamp = bc_report.generated_at if bc_report is not None else None
    console.print(
        f"\n[red]✗ {len(breaking_changes)} breaking contract change(s)[/red]"
        + (f" [dim](detected {stamp})[/dim]" if stamp else "")
        + "[red]:[/red]"
    )
    for c in breaking_changes:
        cross = [ic for ic in c.impacted_consumers if ic.repo != c.provider_repo]
        repos = sorted({ic.repo for ic in cross})
        console.print(
            f"  [red]{escape(c.contract_id)}[/red] ([dim]{escape(c.contract_type)}[/dim]) "
            f"{escape(c.kind)}: {escape(c.detail)}"
        )
        console.print(
            f"      [dim]{escape(c.provider_repo)}/{escape(c.provider_file)} -> "
            f"{len(cross)} consumer(s) in {escape(', '.join(repos))}[/dim]"
        )


def _print_check_pass(report: ConformanceReport, bc_ran: bool) -> None:
    """The pass line, which never calls an unmeasured graph acyclic.

    Not determined is not a violation, so the check still exits 0; the reasons
    are printed above it by :func:`_print_evidence`.
    """
    rule_count = report.rules_evaluated
    state = (report.evidence or {}).get("state")
    mark = "[green]✓[/green]"
    if state == "insufficient":
        mark = "[yellow]?[/yellow]"
        cycles = "dependency cycles not determined (see above)"
    elif state == "partial":
        cycles = "no dependency cycles among the edges detected (partial evidence)"
    else:
        cycles = "no dependency cycles"
    if rule_count:
        console.print(f"\n{mark} No violations of {rule_count} rule(s); {cycles}.")
    else:
        console.print(f"\n{mark} {cycles[0].upper()}{cycles[1:]}.")
    if bc_ran:
        console.print("[green]✓[/green] No breaking contract changes.")
