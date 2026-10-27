"""The doctor table and the pass/fail summary printed after it."""

from __future__ import annotations

from rich.table import Table

from repowise.cli.helpers import console
from repowise.cli.ui.brand import WARN

from ._checks.context import CheckContext
from ._types import DoctorCheck, _status_markup
from .advisories import _advise_claude_md_stamp


def render_checks(ctx: CheckContext, checks: list[DoctorCheck], all_ok: bool) -> None:
    # The "Coordinator drift" row's detail gets its drift percentage colored
    # here, since that highlight is orthogonal to the row's own status.
    table = Table(title=f"repowise Doctor — {ctx.repo_path.name}")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail")
    pct, color = ctx.coord_drift_pct, ctx.coord_drift_color
    for c in checks:
        detail = c.detail
        if c.name == "Coordinator drift":
            detail = detail.replace(f"Drift={pct}", f"Drift=[{color}]{pct}[/{color}]")
        table.add_row(c.name, _status_markup(c.level), detail)
    console.print(table)

    # CLAUDE.md freshness stamp: advisory, never fails the run. If editor-file
    # regeneration stops, the stamp freezes while the index moves on and agents
    # reading the stale "Last indexed" line distrust the tools.
    _advise_claude_md_stamp(ctx.repo_path, ctx.state)

    warnings = sum(c.level == "warn" for c in checks)
    if all_ok and warnings:
        console.print(
            f"[bold green]All checks passed[/bold green], "
            f"[{WARN}]{warnings} warning(s) worth a look.[/{WARN}]"
        )
    elif all_ok:
        console.print("[bold green]All checks passed![/bold green]")
    else:
        console.print("[bold yellow]Some checks failed.[/bold yellow]")
