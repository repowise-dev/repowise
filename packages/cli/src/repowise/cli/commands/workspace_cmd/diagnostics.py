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
    breakdown, unmatched, orphans, in_service = _filter_to_repo(diag, repo_alias)

    if fmt == "json":
        _emit_diagnostics_json(graph, breakdown, unmatched, orphans, in_service)
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
    _print_in_service(in_service)
    _print_orphans(orphans)

    if not unmatched and not orphans:
        console.print(
            "\n  [green]✓[/green] Every consumer matched a provider; no orphan providers."
        )


def _filter_to_repo(
    diag: ExtractionDiagnostics, repo_alias: str | None
) -> tuple[
    list[RepoDiagnostics], list[UnmatchedConsumer], list[OrphanProvider], list[UnmatchedConsumer]
]:
    """The per-repo lists, narrowed to *repo_alias* when one is given."""
    breakdown = diag.repo_breakdown
    unmatched = diag.unmatched_consumers
    orphans = diag.orphan_providers
    in_service = diag.in_service_consumers
    if not repo_alias:
        return breakdown, unmatched, orphans, in_service
    return (
        [r for r in breakdown if r.repo == repo_alias],
        [u for u in unmatched if u.repo == repo_alias],
        [o for o in orphans if o.repo == repo_alias],
        [u for u in in_service if u.repo == repo_alias],
    )


def _emit_diagnostics_json(
    graph: SystemGraph,
    breakdown: list[RepoDiagnostics],
    unmatched: list[UnmatchedConsumer],
    orphans: list[OrphanProvider],
    in_service: list[UnmatchedConsumer],
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
            "in_service_consumers": [u.to_dict() for u in in_service],
            "providers_by_layer": diag.providers_by_layer,
            "consumers_by_layer": diag.consumers_by_layer,
            "http_consumers_unresolved": diag.http_consumers_unresolved,
            "http_consumer_coverage": diag.http_consumer_coverage,
            "http_consumers_linked": diag.http_consumers_linked,
            "http_link_coverage": diag.http_link_coverage,
            "unrecognised_call_files": diag.unrecognised_call_files,
            "topic_names_unresolved": diag.topic_names_unresolved,
            "unrecognised_message_files": diag.unrecognised_message_files,
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
    _print_topic_coverage(diag)
    console.print(
        "    [dim]'index' contracts come from the parsed symbol table, 'regex' "
        "from a text dialect.[/dim]"
    )


def _print_call_coverage(diag: ExtractionDiagnostics) -> None:
    """Linked calls, then read calls, over every call seen; misses named.

    A call read but matched to nothing is not coverage, so the headline leads
    with the calls that linked to another service and says what became of the
    rest. The denominator carries one call per file the network probe found
    calling out with no contract read from it, so an unknown client library
    lowers both figures. An artifact older than the probe gets counts only.
    """
    read = diag.http_consumers_read
    unresolved = diag.http_consumers_unresolved
    missed = diag.unrecognised_call_file_count
    if missed is None:
        console.print(
            f"    HTTP calls  {read} read, {unresolved} located but not "
            "statically resolvable; calls no dialect recognises were not counted "
            "(this artifact predates that count)."
        )
        return
    seen = diag.http_calls_seen
    if seen is None:
        console.print("    HTTP calls  none located, and no file makes a recognisable network call.")
        return
    linked = diag.http_consumers_linked
    if linked is None:
        head = f"{read} of {seen} read ([bold]{read * 100 / seen:.0f}%[/bold]), links not counted"
    else:
        head = (
            f"{linked} of {seen} linked to another service "
            f"([bold]{linked * 100 / seen:.0f}%[/bold]); {read} read ({read * 100 / seen:.0f}%)"
        )
    console.print(
        f"    HTTP calls  {head}: {unresolved} located but not statically "
        f"resolvable, {missed} file(s) calling out through code no dialect reads."
    )
    if linked is not None and read > linked and (split := _unlinked_http_split(diag)):
        console.print(f"      [dim]read, not linked: {split}[/dim]")
    by_repo = [(repo, n) for repo, n in (diag.unrecognised_call_files or {}).items() if n]
    if by_repo:
        console.print(
            "      [dim]unread network calls: "
            + ", ".join(f"{repo} {n}" for repo, n in by_repo)
            + "[/dim]"
        )


def _print_topic_coverage(diag: ExtractionDiagnostics) -> None:
    """Topic contracts read, broker calls not named, and files on a messaging SDK no dialect reads.

    Counts only: a file on an unread SDK is a floor of one call, not a call
    count, so no share is claimed. Silent on an artifact older than the probe.
    """
    missed = diag.unrecognised_message_file_count
    if missed is None:
        return
    read = diag.topic_contracts_read
    unresolved = diag.topic_names_unresolved
    if not (read or unresolved or missed):
        return
    console.print(
        f"    Topic calls {read} read, {unresolved} located but the name not statically "
        f"resolvable, {missed} file(s) using a messaging SDK no dialect reads."
    )
    by_repo = [(repo, n) for repo, n in (diag.unrecognised_message_files or {}).items() if n]
    if by_repo:
        console.print(
            "      [dim]unread messaging calls: "
            + ", ".join(f"{repo} {n}" for repo, n in by_repo)
            + "[/dim]"
        )


def _unlinked_http_split(diag: ExtractionDiagnostics) -> str:
    """Where the read but unlinked HTTP calls went, workspace wide."""
    counts, in_service = diag.http_unlinked()
    parts = [
        f"{n} {_HTTP_REASON_SHORT.get(reason, reason)}"
        for reason, n in sorted(counts.items(), key=lambda kv: _reason_rank(kv[0]))
    ]
    if in_service:
        parts.append(f"{in_service} served inside their own service")
    return ", ".join(parts)


_HTTP_REASON_SHORT = {
    "no_provider": "match no declared route",
    "unlinked": "match a route that did not link",
    "external_host": "call a third-party host",
    "ambiguous_provider": "match a route two or more repos serve",
}

_REASON_LABELS = {
    "no_provider": "dangling: no repo declares the route, service, topic or table they use",
    "unlinked": "matching provider exists but no link formed",
    "external_host": "call a third-party host, outside the workspace",
    "ambiguous_provider": "ambiguous: two or more repos serve the route, nothing names one",
}

#: Reasons in reading order: dangling calls first, any other reason next,
#: third-party hosts last.
_REASON_RANK = {"no_provider": 0, "unlinked": 1, "external_host": 3}

#: Call types listed first; tables collapse to one line per repo.
_TYPE_ORDER = ("http", "grpc", "topic")
_DATA_TYPE = "data"
_ROW_CAP = 20
_NAME_CAP = 12


def _reason_rank(reason: str) -> tuple[int, str]:
    return _REASON_RANK.get(reason, 2), reason


def _type_rank(contract_type: str) -> int:
    return _TYPE_ORDER.index(contract_type) if contract_type in _TYPE_ORDER else len(_TYPE_ORDER)


def _print_rows(rows: list[UnmatchedConsumer] | list[OrphanProvider], indent: str) -> None:
    """Call-shaped rows, HTTP, gRPC and topics first, capped."""
    ordered = sorted(rows, key=lambda r: _type_rank(r.contract_type))
    for r in ordered[:_ROW_CAP]:
        others = getattr(r, "other_files", None)
        extra = f" +{len(others)} more file(s)" if others else ""
        console.print(
            f"{indent}[dim]{r.repo}[/dim] {r.contract_id} ([dim]{r.file_path}{extra}[/dim])"
        )
    if len(ordered) > _ROW_CAP:
        console.print(f"{indent}[dim]... and {len(ordered) - _ROW_CAP} more[/dim]")


def _print_tables(
    rows: list[UnmatchedConsumer] | list[OrphanProvider], indent: str, verb: str, tail: str
) -> None:
    """Table rows as one line per repo: the distinct names, capped."""
    by_repo: dict[str, set[str]] = {}
    for r in rows:
        by_repo.setdefault(r.repo, set()).add(r.contract_id.split("::", 1)[-1])
    for repo, names in sorted(by_repo.items()):
        shown = ", ".join(sorted(names)[:_NAME_CAP])
        more = f", +{len(names) - _NAME_CAP} more" if len(names) > _NAME_CAP else ""
        console.print(
            f"{indent}[dim]{repo}[/dim] {verb} {len(names)} table(s){tail}: {shown}{more}"
        )


def _print_unmatched(unmatched: list[UnmatchedConsumer]) -> None:
    """Unmatched consumers by reason, each call named and tables per repo.

    Counts come from the (possibly filtered) list, so they agree with the total.
    Calls to third-party hosts are counted, not listed: they are out of scope
    on purpose.
    """
    if not unmatched:
        return
    by_reason: dict[str, list[UnmatchedConsumer]] = {}
    for u in unmatched:
        by_reason.setdefault(u.reason, []).append(u)
    console.print(f"\n  [bold]{len(unmatched)}[/bold] unmatched consumer(s):")
    for reason in sorted(by_reason, key=_reason_rank):
        rows = by_reason[reason]
        console.print(f"    [yellow]{len(rows)}[/yellow] {_REASON_LABELS.get(reason, reason)}")
        if reason == "external_host":
            continue
        _print_rows([r for r in rows if r.contract_type != _DATA_TYPE], "      ")
        _print_tables(
            [r for r in rows if r.contract_type == _DATA_TYPE], "      ", "reads", ""
        )


def _print_in_service(in_service: list[UnmatchedConsumer]) -> None:
    """Consumers served by their own service: counted apart, never unmatched."""
    if not in_service:
        return
    by_type = Counter(u.contract_type for u in in_service)
    split = ", ".join(f"{n} {t}" for t, n in by_type.most_common())
    console.print(
        f"\n  [bold]{len(in_service)}[/bold] consumer(s) served inside their own "
        f"service, not cross-repo ({split})."
    )


def _print_orphans(orphans: list[OrphanProvider]) -> None:
    """Providers no consumer reaches: endpoints named, tables one line per repo."""
    if not orphans:
        return
    console.print(f"\n  [bold]{len(orphans)}[/bold] orphan provider(s) (declared, never consumed):")
    _print_rows([o for o in orphans if o.contract_type != _DATA_TYPE], "    ")
    _print_tables(
        [o for o in orphans if o.contract_type == _DATA_TYPE], "    ", "declares", " nothing reads"
    )
