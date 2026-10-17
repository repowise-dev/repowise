"""``repowise workspace check``: architecture lint for CI."""

from __future__ import annotations

import click

from repowise.cli.helpers import console, resolve_repo_path
from repowise.cli.output import emit_json, format_option, json_option, resolve_format

from ._shared import _report_artifact_provenance, _require_workspace
from .group import workspace_group


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

    from rich.markup import escape

    from repowise.core.workspace.breaking_change import (
        SEVERITY_BREAKING,
        load_breaking_change_report,
    )
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
    bc_report = load_breaking_change_report(ws_root) if breaking else None
    bc_ran = bc_report is not None and bc_report.ran
    breaking_changes = (
        [
            c
            for c in bc_report.changes
            if c.severity == SEVERITY_BREAKING
            and any(ic.repo != c.provider_repo for ic in c.impacted_consumers)
        ]
        if bc_ran
        else []
    )

    if fmt == "json":
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
        if report.has_findings or breaking_changes:
            sys.exit(1)
        return

    # State the scope before the verdict. "No cycles" over an empty graph and
    # "no cycles" over 7 services across 3 repos are the same sentence and very
    # different results, and only one of them is a pass.
    _report_artifact_provenance(
        ws_root, ws_config, graph.generated_at, sorted({n.repo for n in graph.nodes})
    )
    console.print(
        f"Checked [bold]{len(graph.nodes)}[/bold] service(s) across "
        f"[bold]{len({n.repo for n in graph.nodes})}[/bold] repo(s) against "
        f"[bold]{report.rules_evaluated}[/bold] declared rule(s)."
    )

    rule_count = report.rules_evaluated
    if rule_count == 0:
        console.print(
            "[dim]No conformance rules declared.[/dim] Add a [bold]conformance:[/bold] "
            "block to .repowise-workspace.yaml to enforce allowed dependencies."
        )

    # Rule violations
    if report.violations:
        console.print(f"\n[red]✗ {len(report.violations)} architecture rule violation(s):[/red]")
        for v in report.violations:
            rule = f"{v.rule_source} !-> {v.rule_target}"
            console.print(
                f"  [red]{v.source}[/red] -> [red]{v.target}[/red] "
                f"([dim]{v.edge_kind}[/dim]) violates [yellow]{rule}[/yellow]"
            )
            if v.rule_description:
                console.print(f"      [dim]{v.rule_description}[/dim]")

    # Dependency cycles
    if report.cycles:
        console.print(f"\n[red]✗ {len(report.cycles)} dependency cycle(s):[/red]")
        for c in report.cycles:
            loop = " -> ".join([*c.nodes, c.nodes[0]]) if c.nodes else ""
            console.print(f"  [red]{loop}[/red]")

    # Breaking contract changes
    if breaking_changes:
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

    if not report.has_findings and not breaking_changes:
        if rule_count:
            console.print(
                f"\n[green]✓[/green] No violations of {rule_count} rule(s); no dependency cycles."
            )
        else:
            console.print("\n[green]✓[/green] No dependency cycles.")
        if bc_ran:
            console.print("[green]✓[/green] No breaking contract changes.")
        return

    # Only claim a breaking-change count when a detection pass produced one.
    tail = f", {len(breaking_changes)} breaking contract change(s)" if bc_ran else ""
    console.print(
        f"\n[red]Architecture check failed:[/red] {len(report.violations)} violation(s), "
        f"{len(report.cycles)} cycle(s){tail}."
    )
    sys.exit(1)
