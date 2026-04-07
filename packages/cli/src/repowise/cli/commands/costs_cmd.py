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
    resolve_repo_path,
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
def costs_command(
    path: str | None,
    since: str | None,
    group_by: str,
    repo_path_flag: str | None,
) -> None:
    """Show LLM cost history for a repository.

    PATH (or --repo-path) defaults to the current directory.
    """
    # Support both positional PATH and --repo-path flag
    raw_path = path or repo_path_flag
    repo_path = resolve_repo_path(raw_path)

    repowise_dir = repo_path / ".repowise"
    if not repowise_dir.exists():
        console.print("[yellow]No .repowise/ directory found. Run 'repowise init' first.[/yellow]")
        return

    since_dt = _parse_date(since)

    rows = run_async(_query_costs(repo_path, since=since_dt, group_by=group_by))

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
