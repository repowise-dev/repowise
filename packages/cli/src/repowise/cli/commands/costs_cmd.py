"""``repowise costs`` — display LLM cost history from the cost ledger."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    get_db_url_for_repo,
    resolve_command_target,
    run_async,
)


def _parse_date(value: str | None) -> datetime | None:
    """Parse an ISO date string into a datetime, or return None."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            from dateutil.parser import parse as _parse  # type: ignore[import-untyped]

            return _parse(value)
        except Exception as exc:
            raise click.BadParameter(f"Cannot parse date '{value}': {exc}") from exc


@click.command("costs")
@click.argument("path", required=False, default=None)
@click.option(
    "--since",
    default=None,
    metavar="DATE",
    help="Only show costs since this date (ISO format, e.g. 2026-01-01).",
)
@click.option(
    "--by",
    "group_by",
    type=click.Choice(["operation", "model", "day"]),
    default="operation",
    show_default=True,
    help="Group costs by operation, model, or day.",
)
@click.option(
    "--repo-path",
    "repo_path_flag",
    default=None,
    metavar="PATH",
    help="Repository path (defaults to current directory).",
)
@click.option(
    "--repo",
    "repo_alias",
    default=None,
    help="Workspace repo alias to query (implies workspace mode).",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="In workspace mode, sum costs across every repo instead of just the primary.",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
def costs_command(
    path: str | None,
    since: str | None,
    group_by: str,
    repo_path_flag: str | None,
    repo_alias: str | None,
    show_all: bool,
    no_workspace: bool,
) -> None:
    """Show LLM cost history for a repository.

    PATH (or --repo-path) defaults to the current directory.

    In workspace mode, defaults to the primary repo; pass --repo <alias>
    for a specific one or --all to sum across every repo.
    """
    # Support both positional PATH and --repo-path flag
    raw_path = path or repo_path_flag

    target = resolve_command_target(
        path=raw_path,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    target.notice(console, command="costs")

    # Resolve which repo paths to query
    repo_paths: list[Path] = []
    if target.is_workspace:
        assert target.ws_root is not None and target.ws_config is not None
        if show_all:
            repo_paths = [(target.ws_root / e.path).resolve() for e in target.ws_config.repos]
        elif target.repo_filter is not None:
            picked = target.resolve_repo_alias(target.repo_filter)
            if picked is None:
                raise click.ClickException(f"Unknown repo alias: {target.repo_filter}")
            repo_paths = [picked]
        else:
            primary = target.primary_path()
            if primary is None:
                raise click.ClickException("Workspace has no primary repo configured.")
            repo_paths = [primary]
    else:
        assert target.repo_path is not None
        repo_paths = [target.repo_path]

    # Filter to repos that have a .repowise/ — otherwise the cost query
    # would just return an empty result and confuse the user.
    valid_paths = [p for p in repo_paths if (p / ".repowise").is_dir()]
    if not valid_paths:
        console.print(
            "[yellow]No indexed .repowise/ directory found. Run 'repowise init' first.[/yellow]"
        )
        return
    if len(valid_paths) < len(repo_paths):
        missing = [p.name for p in repo_paths if p not in valid_paths]
        console.print(f"[yellow]Skipping unindexed repos: {', '.join(missing)}[/yellow]")

    since_dt = _parse_date(since)

    # Aggregate cost rows across every selected repo. For single-repo this
    # is the original behavior; for --all it sums per group across repos.
    rows: list[dict[str, Any]] = []
    if len(valid_paths) == 1:
        rows = run_async(_query_costs(valid_paths[0], since=since_dt, group_by=group_by))
    else:
        merged: dict[str, dict[str, Any]] = {}
        for rp in valid_paths:
            try:
                per_repo = run_async(_query_costs(rp, since=since_dt, group_by=group_by))
            except Exception:
                continue
            for row in per_repo:
                key = str(row.get("group") or "—")
                m = merged.setdefault(
                    key,
                    {
                        "group": key,
                        "calls": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cost_usd": 0.0,
                    },
                )
                m["calls"] += row.get("calls", 0)
                m["input_tokens"] += row.get("input_tokens", 0)
                m["output_tokens"] += row.get("output_tokens", 0)
                m["cost_usd"] += row.get("cost_usd", 0.0)
        rows = sorted(merged.values(), key=lambda r: r["cost_usd"], reverse=True)

    if not rows:
        msg = "No cost records found"
        if since_dt:
            msg += f" since {since_dt.date()}"
        msg += ". Run 'repowise init' with an LLM provider to generate costs."
        console.print(f"[yellow]{msg}[/yellow]")
        return

    # Build table
    group_label = group_by.capitalize()
    table = Table(
        title=f"LLM Costs — grouped by {group_by}",
        border_style="dim",
        show_footer=True,
    )
    table.add_column(group_label, style="cyan", footer="[bold]TOTAL[/bold]")
    table.add_column("Calls", justify="right", footer=str(sum(r["calls"] for r in rows)))
    table.add_column(
        "Input Tokens",
        justify="right",
        footer=f"{sum(r['input_tokens'] for r in rows):,}",
    )
    table.add_column(
        "Output Tokens",
        justify="right",
        footer=f"{sum(r['output_tokens'] for r in rows):,}",
    )
    table.add_column(
        "Cost USD",
        justify="right",
        footer=f"[bold green]${sum(r['cost_usd'] for r in rows):.4f}[/bold green]",
    )

    for row in rows:
        table.add_row(
            str(row["group"] or "—"),
            str(row["calls"]),
            f"{row['input_tokens']:,}",
            f"{row['output_tokens']:,}",
            f"[green]${row['cost_usd']:.4f}[/green]",
        )

    console.print()
    console.print(table)
    console.print()


async def _query_costs(
    repo_path: Path,
    since: datetime | None,
    group_by: str,
) -> list[dict[str, Any]]:
    """Open the DB, look up the repo, and return aggregated cost rows."""
    from repowise.core.generation.cost_tracker import CostTracker
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from repowise.core.persistence.crud import get_repository_by_path

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    sf = create_session_factory(engine)

    try:
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return []

        tracker = CostTracker(session_factory=sf, repo_id=repo.id)
        return await tracker.totals(since=since, group_by=group_by)
    finally:
        await engine.dispose()
