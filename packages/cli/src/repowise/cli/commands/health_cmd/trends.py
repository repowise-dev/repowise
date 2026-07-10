"""Health-trend rendering from the SQLite snapshot history."""

from __future__ import annotations

import json

import click
from rich.table import Table

from repowise.cli.helpers import console, run_async


def _render_trend(repo_path: object, *, fmt: str) -> None:
    """Print the last 10 health snapshots straight from SQLite history.

    Reads through the existing CLI db-url helper so this works on
    workspace and single-repo indexes alike. When no snapshots exist
    (e.g. health was never run), prints a friendly hint.
    """
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.analysis.health.trends import diff_snapshots, recent_kpis
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import (
        get_repository_by_path,
        list_health_snapshots,
    )

    async def _fetch() -> tuple[list[dict], object]:
        url = get_db_url_for_repo(repo_path)
        engine = create_engine(url)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return [], None
            snaps = await list_health_snapshots(session, repo.id)
            return recent_kpis(snaps, limit=10), diff_snapshots(snaps)

    rows, summary = run_async(_fetch())
    if not rows:
        console.print(
            "[yellow]No health snapshots yet. Run `repowise init` or `repowise health` "
            "to populate history.[/yellow]"
        )
        return

    if fmt == "json":
        click.echo(
            json.dumps(
                {
                    "recent": rows,
                    "alerts": [
                        {
                            "kind": a.kind,
                            "metric": a.metric,
                            "current": a.current,
                            "baseline": a.baseline,
                            "delta": a.delta,
                            "message": a.message,
                        }
                        for a in (summary.alerts if summary else [])
                    ],
                },
                indent=2,
            )
        )
        return

    table = Table(title="Code-health snapshots (newest first)")
    table.add_column("Taken at")
    table.add_column("Hotspot", justify="right")
    table.add_column("Average", justify="right")
    table.add_column("Worst", justify="right")
    table.add_column("Worst file", style="dim")
    for r in rows:
        table.add_row(
            (r["taken_at"] or "—")[:19],
            f"{r['hotspot_health']:.2f}",
            f"{r['average_health']:.2f}",
            f"{r['worst_performer_score']:.2f}" if r["worst_performer_score"] is not None else "—",
            r["worst_performer_path"] or "—",
        )
    console.print(table)

    if summary and summary.alerts:
        console.print()
        for a in summary.alerts:
            color = "red" if a.kind == "declining" else "yellow"
            console.print(f"[{color}]⚠ {a.kind}[/{color}]: {a.message}")
