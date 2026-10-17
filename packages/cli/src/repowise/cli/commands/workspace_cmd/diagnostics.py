"""``repowise workspace diagnostics``: explain the cross-repo link count."""

from __future__ import annotations

import click
from rich.table import Table

from repowise.cli.helpers import console, resolve_repo_path
from repowise.cli.output import emit_json, format_option, json_option, resolve_format

from ._shared import _report_artifact_provenance, _require_workspace
from .group import workspace_group


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
    breakdown = diag.repo_breakdown
    unmatched = diag.unmatched_consumers
    orphans = diag.orphan_providers
    if repo_alias:
        breakdown = [r for r in breakdown if r.repo == repo_alias]
        unmatched = [u for u in unmatched if u.repo == repo_alias]
        orphans = [o for o in orphans if o.repo == repo_alias]

    if fmt == "json":
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
            }
        )
        return

    _report_artifact_provenance(
        ws_root, ws_config, graph.generated_at, [r.repo for r in diag.repo_breakdown]
    )

    # Per-repo provider/consumer breakdown
    table = Table(title=f"Contract extraction — {ws_root.name}")
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

    console.print(
        f"\n  [bold]{diag.total_links}[/bold] cross-repo link(s) matched "
        f"from {diag.total_providers} provider(s) and {diag.total_consumers} consumer(s)."
    )
    if diag.weak_link_count:
        console.print(f"  [yellow]{diag.weak_link_count}[/yellow] weak (low-confidence) link(s).")

    # Extraction coverage. Only two honest numbers exist here: which tier
    # produced each contract, and how many client calls were located but not
    # resolved. There is no count of route decorators nobody recognised, so no
    # provider recall percentage is claimed.
    def _layers(counts: dict[str, int]) -> str:
        return ", ".join(f"{n} {layer}" for layer, n in sorted(counts.items()))

    if diag.providers_by_layer or diag.consumers_by_layer:
        console.print("\n  [bold]Extraction coverage[/bold]")
        if diag.providers_by_layer:
            layers = _layers(diag.providers_by_layer)
            console.print(f"    Providers   {diag.total_providers} ({layers})")
        if diag.consumers_by_layer:
            layers = _layers(diag.consumers_by_layer)
            console.print(f"    Consumers   {diag.total_consumers} ({layers})")
        coverage = diag.http_consumer_coverage
        if coverage is not None:
            http_consumers = sum(r.consumers_by_type.get("http", 0) for r in diag.repo_breakdown)
            console.print(
                f"    HTTP calls  {http_consumers} of "
                f"{http_consumers + diag.http_consumers_unresolved} resolved to an "
                f"endpoint ([bold]{coverage * 100:.0f}%[/bold]); "
                f"{diag.http_consumers_unresolved} located but not statically resolvable."
            )
        console.print(
            "    [dim]'index' contracts come from the parsed symbol table, 'regex' "
            "from a text dialect. Calls no dialect recognises are not counted here.[/dim]"
        )

    # Unmatched consumers grouped by reason
    if unmatched:
        reason_labels = {
            "no_provider": "no matching provider found",
            "internal_only": "provider is same repo + service (intra-service)",
            "unlinked": "matching provider exists but no link formed",
        }
        console.print(f"\n  [bold]{len(unmatched)}[/bold] unmatched consumer(s):")
        for reason, count in sorted(diag.unmatched_by_reason.items()):
            label = reason_labels.get(reason, reason)
            console.print(f"    [yellow]{count}[/yellow] — {label}")

    # Orphan providers
    if orphans:
        console.print(
            f"\n  [bold]{len(orphans)}[/bold] orphan provider(s) (declared, never consumed):"
        )
        for o in orphans[:20]:
            console.print(f"    [dim]{o.repo}[/dim] {o.contract_id} ([dim]{o.file_path}[/dim])")
        if len(orphans) > 20:
            console.print(f"    [dim]... and {len(orphans) - 20} more[/dim]")

    if not unmatched and not orphans:
        console.print(
            "\n  [green]✓[/green] Every consumer matched a provider; no orphan providers."
        )
