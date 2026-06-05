"""``repowise saved`` — report tokens saved by output distillation.

Reads the savings ledger in the omissions sidecar
(``.repowise/omissions/omissions.db``). The ledger covers the
``repowise distill`` path only — both direct invocations and hook rewrites.
MCP response truncation is deliberately not recorded: those responses were
always budget-capped, so nothing was "saved" relative to before.

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
def saved_command(
    path: str | None,
    group_by: str,
    since: str | None,
    pricing_model: str,
) -> None:
    """Show tokens (and estimated dollars) saved by ``repowise distill``.

    PATH defaults to the current directory; the report covers that repo's
    omission store (or the user-level fallback store when the repo has no
    ``.repowise/``). Covers the distill command/hook path only — MCP response
    truncation is not part of this ledger.
    """
    from repowise.core.distill.store import OmissionStore, default_store_path

    since_ts = _parse_since(since)

    start = Path(path).resolve() if path else Path.cwd()
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
            "Covers the 'repowise distill' command/hook path only; "
            "MCP response truncation is not counted."
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
