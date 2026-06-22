"""``repowise saved`` — report tokens saved by output distillation.

Reads the savings ledger in the omissions sidecar
(``.repowise/omissions/omissions.db``). The ledger covers the
``repowise distill`` path (direct invocations and hook rewrites) plus MCP
counterfactual savings — each tool answer priced against the raw file
exploration it replaced, recorded under ``source='mcp:<tool>'``. Group by
source to split the two surfaces.

Named ``saved`` rather than ``distill --stats`` because ``repowise distill``
captures everything after it as the command to run (``ignore_unknown_options``)
— a ``--stats`` flag there would be indistinguishable from a command named
``--stats``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import console

#: Pricing model used for the dollar estimate. Saved tokens are input-side
#: tokens the coding agent never had to read, so the input rate applies.
DEFAULT_PRICING_MODEL = "claude-sonnet-4-6"


@click.command("saved")
@click.argument("path", required=False, default=None)
@click.option(
    "--by",
    "group_by",
    type=click.Choice(["filter", "day", "source"]),
    default="filter",
    show_default=True,
    help="Group savings by filter, day, or source surface.",
)
@click.option(
    "--since",
    default=None,
    metavar="DATE",
    help="Only count savings since this date (ISO format, e.g. 2026-01-01).",
)
@click.option(
    "--model",
    "pricing_model",
    default=DEFAULT_PRICING_MODEL,
    show_default=True,
    metavar="MODEL",
    help="Pricing model for the dollar estimate (input-token rate).",
)
@click.option(
    "--missed",
    "show_missed",
    is_flag=True,
    help="Report savings foregone by raw (non-distilled) agent commands.",
)
@click.option(
    "--missed-days",
    type=click.FloatRange(min=0.1),
    default=7.0,
    show_default=True,
    metavar="DAYS",
    help="Transcript window for the missed-savings scan.",
)
def saved_command(
    path: str | None,
    group_by: str,
    since: str | None,
    pricing_model: str,
    show_missed: bool,
    missed_days: float,
) -> None:
    """Show tokens (and estimated dollars) saved by ``repowise distill``.

    PATH defaults to the current directory; the report covers that repo's
    omission store (or the user-level fallback store when the repo has no
    ``.repowise/``). Covers the distill command/hook path plus MCP
    counterfactual savings (``source='mcp:<tool>'``); ``--by source`` splits them.
    """
    from repowise.core.distill.store import OmissionStore, default_store_path

    since_ts = _parse_since(since)

    start = Path(path).resolve() if path else Path.cwd()

    if show_missed:
        _print_missed_report(start, missed_days, pricing_model)
        return

    db_path = default_store_path(start)
    if not db_path.exists():
        console.print(
            "[yellow]No savings recorded yet.[/yellow] Run commands through "
            "'repowise distill <cmd>' (or install the rewrite hook with "
            "'repowise hook rewrite install') to start saving tokens."
        )
        return

    store = OmissionStore(db_path)
    try:
        summary = store.savings_summary(since=since_ts)
        rows = store.savings_rollup(by=group_by, since=since_ts)
    finally:
        store.close()

    if summary["events"] == 0:
        msg = "No distillation events recorded"
        if since_ts is not None:
            msg += f" since {since}"
        console.print(f"[yellow]{msg}.[/yellow]")
        return

    saved = summary["saved_tokens"]
    pct = 100.0 * saved / summary["raw_tokens"] if summary["raw_tokens"] else 0.0
    usd, rate = _estimate_usd(saved, pricing_model)

    table = Table(
        title=f"Distill savings - grouped by {group_by}",
        border_style="dim",
        show_footer=True,
        caption=(
            "Covers the 'repowise distill' command/hook path plus MCP "
            "counterfactual savings (mcp:<tool>); group by source to split them."
        ),
    )
    table.add_column(group_by.capitalize(), style="cyan", footer="[bold]TOTAL[/bold]")
    table.add_column("Events", justify="right", footer=str(summary["events"]))
    table.add_column("Raw Tokens", justify="right", footer=f"{summary['raw_tokens']:,}")
    table.add_column("Distilled Tokens", justify="right", footer=f"{summary['distilled_tokens']:,}")
    table.add_column(
        "Saved Tokens",
        justify="right",
        footer=f"[bold green]{saved:,} ({pct:.0f}%)[/bold green]",
    )
    for row in rows:
        row_pct = 100.0 * row["saved_tokens"] / row["raw_tokens"] if row["raw_tokens"] else 0.0
        table.add_row(
            str(row["group"] or "-"),
            str(row["events"]),
            f"{row['raw_tokens']:,}",
            f"{row['distilled_tokens']:,}",
            f"[green]{row['saved_tokens']:,} ({row_pct:.0f}%)[/green]",
        )

    console.print()
    console.print(table)
    console.print(
        f"  Estimated saved: [bold green]${usd:.4f}[/bold green] "
        f"[dim](at ${rate:.2f}/M input tokens, {pricing_model}; "
        f"tokens are chars/4 estimates)[/dim]"
    )
    console.print(f"  [dim]Ledger: {db_path}[/dim]")
    _print_missed_summary_line(start, missed_days)
    _print_reread_summary_line(start, missed_days)
    console.print()


def _missed_report(start: Path, days: float) -> dict | None:
    """Best-effort missed-savings scan rooted at the enclosing repowise repo."""
    try:
        from repowise.cli.helpers import find_repowise_repo_root
        from repowise.core.distill.missed import scan_missed_savings

        repo_root = find_repowise_repo_root(start) or start
        return scan_missed_savings(repo_root, days=days)
    except Exception:
        return None


def _print_missed_summary_line(start: Path, days: float) -> None:
    """One foregone-savings line under the main report; silent when empty."""
    report = _missed_report(start, days)
    if not report or not report["events"]:
        return
    console.print(
        f"  Missed: [yellow]~{report['est_saved_tokens']:,} tokens[/yellow] across "
        f"{report['events']} raw command runs in the last {days:g} days "
        f"[dim](repowise saved --missed)[/dim]"
    )


def _reread_report(start: Path, days: float) -> dict | None:
    """Best-effort wasteful-re-read scan rooted at the enclosing repowise repo."""
    try:
        from repowise.cli.helpers import find_repowise_repo_root
        from repowise.core.distill.missed_mcp import scan_missed_mcp_savings

        repo_root = find_repowise_repo_root(start) or start
        return scan_missed_mcp_savings(repo_root, days=days)
    except Exception:
        return None


def _print_reread_summary_line(start: Path, days: float) -> None:
    """One re-read-waste line under the main report; silent when empty."""
    report = _reread_report(start, days)
    if not report or not report["events"]:
        return
    console.print(
        f"  Re-reads: [yellow]~{report['est_saved_tokens']:,} tokens[/yellow] across "
        f"{report['events']} full re-reads of unchanged files in the last {days:g} days "
        f"[dim](repowise saved --missed)[/dim]"
    )


def _print_missed_report(start: Path, days: float, pricing_model: str) -> None:
    missed = _missed_report(start, days)
    reread = _reread_report(start, days)
    has_missed = bool(missed and missed["events"])
    has_reread = bool(reread and reread["events"])

    if not has_missed and not has_reread:
        console.print(
            f"[yellow]No missed savings found in the last {days:g} days.[/yellow] "
            "Either every distillable command already ran through 'repowise distill' "
            "and no files were needlessly re-read, or no agent transcripts cover this repo."
        )
        return

    if has_missed:
        _render_missed_distill_table(missed, days, pricing_model)
    if has_reread:
        _render_reread_table(reread, days, pricing_model)


def _render_missed_distill_table(report: dict, days: float, pricing_model: str) -> None:
    usd, rate = _estimate_usd(report["est_saved_tokens"], pricing_model)
    table = Table(
        title=f"Missed distill savings - last {days:g} days",
        border_style="dim",
        show_footer=True,
        caption=(
            "Raw agent commands a filter would have caught; estimates use each "
            "filter's conservative fixture floor. Scanned from local Claude Code "
            "transcripts - nothing leaves this machine."
        ),
    )
    table.add_column("Family", style="cyan", footer="[bold]TOTAL[/bold]")
    table.add_column("Events", justify="right", footer=str(report["events"]))
    table.add_column("Raw Tokens", justify="right", footer=f"{report['raw_tokens']:,}")
    table.add_column(
        "Est. Foregone",
        justify="right",
        footer=f"[bold yellow]{report['est_saved_tokens']:,}[/bold yellow]",
    )
    for family, stats in report["per_filter"].items():
        table.add_row(
            family,
            str(stats["events"]),
            f"{stats['raw_tokens']:,}",
            f"[yellow]{stats['est_saved_tokens']:,}[/yellow]",
        )

    console.print()
    console.print(table)
    console.print(
        f"  Estimated foregone: [bold yellow]${usd:.4f}[/bold yellow] "
        f"[dim](at ${rate:.2f}/M input tokens, {pricing_model}; "
        f"tokens are chars/4 estimates)[/dim]"
    )
    console.print(
        "  [dim]Tip: install the rewrite hook ('repowise hook rewrite install') "
        "to catch these automatically.[/dim]"
    )
    console.print()


def _render_reread_table(report: dict, days: float, pricing_model: str) -> None:
    usd, rate = _estimate_usd(report["est_saved_tokens"], pricing_model)
    table = Table(
        title=f"Missed MCP savings (file re-reads) - last {days:g} days",
        border_style="dim",
        show_footer=True,
        caption=(
            "Full re-reads of unchanged files a targeted get_symbol / range read "
            "would have replaced; estimates credit a conservative half of each "
            "re-read. Scanned from local Claude Code transcripts - nothing leaves "
            "this machine."
        ),
    )
    table.add_column("File", style="cyan", footer="[bold]TOTAL[/bold]")
    table.add_column("Re-reads", justify="right", footer=str(report["events"]))
    table.add_column("Raw Tokens", justify="right", footer=f"{report['raw_tokens']:,}")
    table.add_column(
        "Est. Foregone",
        justify="right",
        footer=f"[bold yellow]{report['est_saved_tokens']:,}[/bold yellow]",
    )
    for rel, stats in list(report["per_file"].items())[:15]:
        table.add_row(
            rel,
            str(stats["events"]),
            f"{stats['raw_tokens']:,}",
            f"[yellow]{stats['est_saved_tokens']:,}[/yellow]",
        )

    console.print()
    console.print(table)
    console.print(
        f"  Estimated foregone: [bold yellow]${usd:.4f}[/bold yellow] "
        f"[dim](at ${rate:.2f}/M input tokens, {pricing_model}; "
        f"tokens are chars/4 estimates)[/dim]"
    )
    console.print(
        '  [dim]Tip: for a known symbol use get_symbol("file::Name") or a '
        "line-range read instead of re-reading the whole file.[/dim]"
    )
    console.print()


def _parse_since(value: str | None) -> float | None:
    """ISO date string -> Unix timestamp, or None."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError as exc:
        raise click.BadParameter(f"Cannot parse date '{value}': {exc}") from exc


def _estimate_usd(saved_tokens: int, model: str) -> tuple[float, float]:
    """Dollar estimate for *saved_tokens* at *model*'s input rate."""
    from repowise.core.generation.cost_tracker import get_model_pricing

    rate = get_model_pricing(model)["input"]
    return saved_tokens * rate / 1_000_000, rate
