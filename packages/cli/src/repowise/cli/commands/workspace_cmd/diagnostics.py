"""``repowise workspace diagnostics``: explain the cross-repo link count."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import click
from rich.table import Table

from repowise.cli.helpers import console, resolve_repo_path
from repowise.cli.output import emit_json, format_option, json_option, resolve_format

from ._shared import _print_evidence, _report_artifact_provenance, _require_workspace
from .group import workspace_group

if TYPE_CHECKING:
    from repowise.core.workspace.diagnostics import (
        ExtractionDiagnostics,
        OrphanProvider,
        RepoDiagnostics,
        UnmatchedConsumer,
    )
    from repowise.core.workspace.system_graph import SystemGraph


@workspace_group.command("diagnostics")
@click.argument("path", required=False, default=None)
@click.option("--repo", "repo_alias", default=None, help="Limit the report to one repo alias.")
@format_option(help="Output format. ``json`` emits the raw diagnostics.")
@json_option()
def workspace_diagnostics(
    path: str | None, repo_alias: str | None, fmt: str, as_json: bool
) -> None:
    """Explain the cross-repo contract link count.

    Reports, per repo, how many providers and consumers were found, which
    consumers went unmatched and why, and which providers have no consumer —
    the answer to "why are there so few links?". Reads the system graph built
    during 'repowise update --workspace'.
    """
    from repowise.core.workspace.graph_evidence import graph_evidence
    from repowise.core.workspace.system_graph import load_system_graph

    fmt = resolve_format(fmt, as_json)
    start = resolve_repo_path(path)
    ws_root, ws_config = _require_workspace(start)

    graph = load_system_graph(ws_root)
    if graph is None:
        raise click.ClickException(
            "No system graph found. Run 'repowise update --workspace' to build "
            "cross-repo contracts and diagnostics first."
        )

    diag = graph.diagnostics
    breakdown, unmatched, orphans = _filter_to_repo(diag, repo_alias)

    if fmt == "json":
        _emit_diagnostics_json(graph, breakdown, unmatched, orphans)
        return

    _report_artifact_provenance(
        ws_root,
        ws_config,
        graph.generated_at,
        [r.repo for r in diag.repo_breakdown],
        graph.repo_provenance,
    )
    _print_evidence(graph_evidence(graph).to_dict())
    _print_breakdown_table(ws_root.name, breakdown)
    _print_link_totals(diag)
    _print_extraction_coverage(diag)
    _print_unmatched(unmatched)
    _print_orphans(orphans)

    if not unmatched and not orphans:
        console.print(
            "\n  [green]✓[/green] Every consumer matched a provider; no orphan providers."
        )


def _filter_to_repo(
    diag: ExtractionDiagnostics, repo_alias: str | None
) -> tuple[list[RepoDiagnostics], list[UnmatchedConsumer], list[OrphanProvider]]:
    """The per-repo lists, narrowed to *repo_alias* when one is given."""
    breakdown = diag.repo_breakdown
    unmatched = diag.unmatched_consumers
    orphans = diag.orphan_providers
    if not repo_alias:
        return breakdown, unmatched, orphans
    return (
        [r for r in breakdown if r.repo == repo_alias],
        [u for u in unmatched if u.repo == repo_alias],
        [o for o in orphans if o.repo == repo_alias],
    )


def _emit_diagnostics_json(
    graph: SystemGraph,
    breakdown: list[RepoDiagnostics],
    unmatched: list[UnmatchedConsumer],
    orphans: list[OrphanProvider],
) -> None:
    from repowise.core.workspace.graph_evidence import graph_evidence

    diag = graph.diagnostics
    emit_json(
        {
            "generated_at": graph.generated_at or None,
            "total_providers": diag.total_providers,
            "total_consumers": diag.total_consumers,
            "total_links": diag.total_links,
            "weak_link_count": diag.weak_link_count,
            "repo_breakdown": [r.to_dict() for r in breakdown],
            "unmatched_consumers": [u.to_dict() for u in unmatched],
            "unmatched_by_reason": diag.unmatched_by_reason,
            "orphan_providers": [o.to_dict() for o in orphans],
            "providers_by_layer": diag.providers_by_layer,
            "consumers_by_layer": diag.consumers_by_layer,
            "http_consumers_unresolved": diag.http_consumers_unresolved,
            "http_consumer_coverage": diag.http_consumer_coverage,
            "unrecognised_call_files": diag.unrecognised_call_files,
            "evidence": graph_evidence(graph).to_dict(),
        }
    )


def _print_breakdown_table(ws_name: str, breakdown: list[RepoDiagnostics]) -> None:
    """Per-repo provider/consumer breakdown."""
    table = Table(title=f"Contract extraction — {ws_name}")
    table.add_column("Repo", style="cyan")
    table.add_column("Providers", justify="right")
    table.add_column("Consumers", justify="right")
    table.add_column("By type", style="dim")
    for r in breakdown:
        by_type = ", ".join(
            f"{t}:{r.providers_by_type.get(t, 0)}/{r.consumers_by_type.get(t, 0)}"
            for t in sorted(set(r.providers_by_type) | set(r.consumers_by_type))
        )
        table.add_row(r.repo, str(r.provider_count), str(r.consumer_count), by_type or "-")
    console.print(table)


def _print_link_totals(diag: ExtractionDiagnostics) -> None:
    console.print(
        f"\n  [bold]{diag.total_links}[/bold] cross-repo link(s) matched "
        f"from {diag.total_providers} provider(s) and {diag.total_consumers} consumer(s)."
    )
    if diag.weak_link_count:
        console.print(f"  [yellow]{diag.weak_link_count}[/yellow] weak (low-confidence) link(s).")


def _layers(counts: dict[str, int]) -> str:
    return ", ".join(f"{n} {layer}" for layer, n in sorted(counts.items()))


def _print_extraction_coverage(diag: ExtractionDiagnostics) -> None:
    """Extraction coverage.

    Only two honest numbers exist here: which tier produced each contract, and
    how many client calls were located but not resolved. There is no count of
    route decorators nobody recognised, so no provider recall percentage is
    claimed.
    """
    if not (diag.providers_by_layer or diag.consumers_by_layer):
        return
    console.print("\n  [bold]Extraction coverage[/bold]")
    if diag.providers_by_layer:
        layers = _layers(diag.providers_by_layer)
        console.print(f"    Providers   {diag.total_providers} ({layers})")
    if diag.consumers_by_layer:
        layers = _layers(diag.consumers_by_layer)
        console.print(f"    Consumers   {diag.total_consumers} ({layers})")
    _print_call_coverage(diag)
    console.print(
        "    [dim]'index' contracts come from the parsed symbol table, 'regex' "
        "from a text dialect.[/dim]"
    )


def _print_call_coverage(diag: ExtractionDiagnostics) -> None:
    """Resolved calls over every call seen, misses named per repo.

    The denominator carries one call per file the network probe found calling
    out with no contract read from it, so an unknown client library lowers the
    figure. An artifact older than the probe gets counts and no percentage.
    """
    http_consumers = sum(r.consumers_by_type.get("http", 0) for r in diag.repo_breakdown)
    unresolved = diag.http_consumers_unresolved
    missed = diag.unrecognised_call_file_count
    coverage = diag.http_consumer_coverage
    if missed is None:
        console.print(
            f"    HTTP calls  {http_consumers} resolved, {unresolved} located but not "
            "statically resolvable; calls no dialect recognises were not counted "
            "(this artifact predates that count)."
        )
        return
    if coverage is None:
        console.print("    HTTP calls  none located, and no file makes a recognisable network call.")
        return
    console.print(
        f"    HTTP calls  {http_consumers} of {http_consumers + unresolved + missed} resolved "
        f"([bold]{coverage * 100:.0f}%[/bold]): {unresolved} located but not statically "
        f"resolvable, {missed} file(s) calling out through code no dialect reads."
    )
    by_repo = [(repo, n) for repo, n in (diag.unrecognised_call_files or {}).items() if n]
    if by_repo:
        console.print(
            "      [dim]unread network calls: "
            + ", ".join(f"{repo} {n}" for repo, n in by_repo)
            + "[/dim]"
        )


_REASON_LABELS = {
    "no_provider": "no matching provider found",
    "internal_only": "provider is same repo + service (intra-service)",
    "unlinked": "matching provider exists but no link formed",
}


def _print_unmatched(unmatched: list[UnmatchedConsumer]) -> None:
    """Unmatched consumers grouped by reason.

    Counts come from the (possibly filtered) list, so they agree with the total.
    """
    if not unmatched:
        return
    by_reason = Counter(u.reason for u in unmatched)
    console.print(f"\n  [bold]{len(unmatched)}[/bold] unmatched consumer(s):")
    for reason, count in sorted(by_reason.items()):
        label = _REASON_LABELS.get(reason, reason)
        console.print(f"    [yellow]{count}[/yellow] — {label}")


def _print_orphans(orphans: list[OrphanProvider]) -> None:
    if not orphans:
        return
    console.print(f"\n  [bold]{len(orphans)}[/bold] orphan provider(s) (declared, never consumed):")
    for o in orphans[:20]:
        console.print(f"    [dim]{o.repo}[/dim] {o.contract_id} ([dim]{o.file_path}[/dim])")
    if len(orphans) > 20:
        console.print(f"    [dim]... and {len(orphans) - 20} more[/dim]")
