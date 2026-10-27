"""``repowise workspace impacted-tests``: cross-repo test impact."""

from __future__ import annotations

import click
from rich.table import Table

from repowise.cli._setup import configure_cli_logging
from repowise.cli.helpers import console, resolve_repo_path, run_async
from repowise.cli.output import emit_json, format_option, json_option, resolve_format

from ._shared import _require_workspace
from .group import workspace_group

_UNRESOLVED_REASONS = {
    "no_index": "consumer has no index",
    "unbound": "contract never bound to a symbol",
    "symbol_missing": "bound symbol is not in the index",
}

_EMPTY_REASONS = {
    "no_contract_store": "there is no contract map yet; run 'repowise update --workspace'",
    "no_matching_links": "no contract link connects the changed files to a consumer",
    "no_contract_links": (
        "could not determine; the contract map links no consumer to any provider in "
        "this workspace (see 'repowise workspace diagnostics')"
    ),
    "no_changed_files": "no changed files were given",
}


def _unresolved_reason_text(reason: str, detail: str | None) -> str:
    """Plain words for why a contract link could not be followed."""
    if reason == "lookup_failed":
        return f"lookup failed ({detail or 'unknown'})"
    return _UNRESOLVED_REASONS.get(reason, reason)


def _empty_explanation(result: object) -> str:
    """Say which state produced an empty answer, never just that it is empty."""
    summary = getattr(result, "summary", {}) or {}
    reason = summary.get("reason")
    if reason:
        return f"No tests found: {_EMPTY_REASONS.get(reason, reason)}."
    parts = []
    reached_nothing = (summary.get("states") or {}).get("none", 0)
    if reached_nothing:
        parts.append(f"{reached_nothing} consumer file(s) the requested passes found nothing for")
    unresolved = len(getattr(result, "unresolved", []) or [])
    if unresolved:
        parts.append(f"{unresolved} link(s) could not be determined")
    passes = summary.get("passes") or {}
    disabled = [name for name in ("measured", "inferred") if passes.get(name) is False]
    suffix = "".join(f" ({name} pass disabled)" for name in disabled)
    if not parts:
        return f"No tests found: no consumer call site was analysed.{suffix}"
    return f"No tests found: {', '.join(parts)}.{suffix}"


@workspace_group.command("impacted-tests")
@click.argument("changed_files", required=True, nargs=-1)
@click.option(
    "--path",
    "workspace_path",
    default=None,
    help="Path to workspace root (default: auto-detect from cwd).",
)
@click.option(
    "--call-depth",
    default=3,
    type=click.IntRange(1, 8),
    help="Call graph walk depth (default: 3).",
)
@click.option(
    "--import-depth",
    default=1,
    type=click.IntRange(1, 3),
    help="Import graph fallback depth (default: 1).",
)
@click.option(
    "--no-measured",
    is_flag=True,
    help="Exclude measured coverage-backed recommendations.",
)
@click.option(
    "--no-inferred",
    is_flag=True,
    help="Exclude graph-inferred recommendations.",
)
@click.option(
    "--min-confidence",
    default=0.0,
    type=click.FloatRange(0.0, 1.0),
    help="Minimum contract link confidence to consider (0.0-1.0).",
)
@click.option(
    "--target-repo",
    "target_repos",
    multiple=True,
    help="Limit analysis to these consumer repo aliases. Repeatable.",
)
@format_option(choices=("table", "json", "list"))
@json_option()
def workspace_impacted_tests(
    changed_files: tuple[str, ...],
    workspace_path: str | None,
    call_depth: int,
    import_depth: int,
    no_measured: bool,
    no_inferred: bool,
    min_confidence: float,
    target_repos: tuple[str, ...],
    fmt: str,
    as_json: bool,
) -> None:
    """Cross-repository test impact: which downstream tests to run.

    Given a list of changed files in provider repositories (format: repo:path),
    returns the test files in consumer repositories that cover those changes,
    via measured per-test coverage, call-graph reachability, and import-graph
    fallback.

    Example:
        repowise workspace impacted-tests backend-api:src/api/users.py

    Output format:
        - table (default): human-readable grouped by consumer repo
        - json: full machine-readable result
        - list: one repo:path line per test file
    """
    configure_cli_logging()
    fmt = resolve_format(fmt, as_json)

    if not changed_files:
        raise click.ClickException(
            "At least one changed file required. Format: repo_alias:path/to/file.py"
        )

    # Parse changed_files
    parsed_changed: list[dict[str, str]] = []
    for cf in changed_files:
        if ":" not in cf:
            raise click.ClickException(
                f"Invalid format: {cf}. Use repo_alias:path/to/file.py"
            )
        repo, path = cf.split(":", 1)
        parsed_changed.append({"repo": repo, "path": path})

    start = resolve_repo_path(workspace_path)
    ws_root, _ = _require_workspace(start)

    from repowise.core.workspace.test_impact import (
        MAX_TESTS_PER_TARGET,
        workspace_test_impact_from_root,
        workspace_test_impact_to_dict,
    )

    result = run_async(
        workspace_test_impact_from_root(
            ws_root,
            parsed_changed,
            call_depth=call_depth,
            import_depth=import_depth,
            include_measured=not no_measured,
            include_inferred=not no_inferred,
            min_confidence=min_confidence,
            target_repos=list(target_repos) if target_repos else None,
        )
    )

    if fmt == "json":
        emit_json(workspace_test_impact_to_dict(result))
        return

    if fmt == "list":
        # Output test file paths only, one per line
        seen: set[str] = set()
        for rec in result.recommendations:
            key = f"{rec.consumer_repo}:{rec.test_file}"
            if key not in seen:
                seen.add(key)
                click.echo(key)
        if not seen:
            # A pipeline reading stdout must never get silence with exit 0.
            click.echo(_empty_explanation(result), err=True)
        if result.unresolved:
            click.echo(
                f"{len(result.unresolved)} contract link(s) could not be determined; "
                "run with --format table to see why.",
                err=True,
            )
        return

    # Group by consumer repo
    by_consumer: dict[str, list] = {}
    for rec in result.recommendations:
        by_consumer.setdefault(rec.consumer_repo, []).append(rec)

    console.print(f"[bold]Workspace test impact[/bold] for {len(parsed_changed)} changed file(s):")
    for item in parsed_changed:
        console.print(f"  [dim]{item['repo']}:{item['path']}[/dim]")

    for consumer_repo, recs in sorted(by_consumer.items()):
        table = Table(title=f"Consumer: {consumer_repo}")
        table.add_column("Test File", style="green")
        table.add_column("Basis", style="yellow")
        table.add_column("Via", style="dim")
        table.add_column("Provider Repo", style="cyan")
        table.add_column("Provider Files", style="dim")
        table.add_column("Contract", style="dim")
        table.add_column("Confidence", justify="right")

        for rec in recs:
            table.add_row(
                rec.test_file,
                rec.basis,
                rec.via,
                rec.provider_repo,
                ", ".join(rec.source_files),
                ", ".join(rec.contract_ids),
                f"{rec.confidence:.2f}",
            )
        console.print(table)

    if result.unresolved:
        unresolved_table = Table(title="Could not determine")
        unresolved_table.add_column("Consumer", style="cyan")
        unresolved_table.add_column("File", style="dim")
        unresolved_table.add_column("Contract", style="dim")
        unresolved_table.add_column("Reason", style="yellow")
        for link in result.unresolved:
            unresolved_table.add_row(
                link.consumer_repo,
                link.consumer_file,
                link.contract_id,
                _unresolved_reason_text(link.reason, link.detail),
            )
        console.print(unresolved_table)

    if result.recommendations_emitted == 0:
        console.print(f"[yellow]{_empty_explanation(result)}[/yellow]")
        return

    by_basis = result.recommendations_by_basis
    console.print(
        f"\n[bold]{result.recommendations_emitted}[/bold] test recommendation(s) across "
        f"[bold]{len(by_consumer)}[/bold] consumer repo(s):"
    )
    if by_basis.get("measured", 0):
        console.print(f"  [green]{by_basis['measured']}[/green] from measured coverage")
    if by_basis.get("inferred", 0):
        console.print(f"  [yellow]{by_basis['inferred']}[/yellow] inferred from call/import graph")
    if result.recommendations_truncated:
        console.print(
            f"  [dim]{result.recommendations_omitted} more omitted "
            f"(cap {MAX_TESTS_PER_TARGET} per consumer and provider pair); "
            f"use --format json for counts.[/dim]"
        )
