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

#: Fallback pricing model for the dollar estimate. Saved tokens are input-side
#: tokens the coding agent never had to read, so the input rate applies. Used
#: only when no agent session can be detected — see :func:`_resolve_pricing`.
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
    default=None,
    metavar="MODEL",
    help=(
        "Pricing model for the dollar estimate (input-token rate). Defaults to "
        "the model detected from this repo's most recent agent session, and "
        f"falls back to {DEFAULT_PRICING_MODEL} when there is none."
    ),
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
    pricing_model: str | None,
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
    pricing_model, pricing_note = _resolve_pricing(start, pricing_model)

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
        # Before returning, not after. Declining the init prompt turns off
        # distill rewrites *and* skeleton-served Reads in the same write, so
        # the cohort the counterfactual exists to inform is exactly the cohort
        # with zero distillation events — and returning here first would make
        # its rows unreadable by the only command that reports them.
        _print_forgone_read_skeleton_line(start, db_path, since_ts)
        return

    saved = summary["saved_tokens"]
    pct = 100.0 * saved / summary["raw_tokens"] if summary["raw_tokens"] else 0.0
    usd, rate = _estimate_usd(saved, pricing_model)

    table = Table(
        title=f"Distill savings - grouped by {group_by}",
        border_style="dim",
        show_footer=True,
        caption=(
            "Covers the 'repowise distill' command/hook path, MCP "
            "counterfactual savings (mcp:<tool>), and the Read hook serving a "
            "skeleton in place of a whole file (read_skeleton / hook-read); "
            "group by source to split them."
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
        f"[dim](at ${rate:.2f}/M input tokens, {pricing_note}; "
        f"tokens are chars/4 estimates)[/dim]"
    )
    console.print(f"  [dim]Ledger: {db_path}[/dim]")
    _print_mcp_truncation_line(db_path, since_ts)
    _print_missed_summary_line(start, missed_days)
    _print_reread_summary_line(start, missed_days)
    _print_forgone_read_skeleton_line(start, db_path, since_ts)
    console.print()


#: One entry per replacing hook surface: the savings-ledger source that tags
#: its forgone rows, how to read its on/off verdict, and how to name it. The
#: source filter is load-bearing: every surface writes into the one
#: ``forgone_savings`` table, so an unfiltered sum would report a search
#: saving as a Read one.
_FORGONE_SURFACES = (
    (
        "hook-read",
        "read_skeleton",
        "skeleton-served Reads",
        "file",
        "repowise hook read-skeleton install",
    ),
    (
        "hook-search",
        "search_digest",
        "digest-served searches",
        "search",
        "repowise hook search-digest install",
    ),
)


def _print_mcp_truncation_line(db_path: Path, since_ts: float | None) -> None:
    """MCP savings the table above structurally cannot show.

    Two MCP signals exist. Counterfactual rows (``source='mcp:<tool>'`` in
    ``savings``) are already in the table, because they went through
    ``record_saving`` like everything else. Truncation drops are not: a tool
    with no counterfactual estimator writes only to ``omissions``, never
    calls ``record_saving``, and so is invisible to ``savings_summary`` --
    real savings that happened, sitting one table over.

    They are printed rather than folded into the footer because the table's
    columns are raw/distilled pairs and a drop has no raw counterpart to
    put in them. ``mcp_savings_summary`` merges with counterfactual
    precedence, so taking only the ``truncation`` rows adds each tool once.
    """
    import sqlite3

    from repowise.core.distill import tracking

    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            summary = tracking.mcp_savings_summary(con, since=since_ts)
        finally:
            con.close()
    except sqlite3.Error:
        return  # no such table: this repo has never served MCP, not an error

    rows = [r for r in summary["per_tool"] if r["kind"] == "truncation"]
    if not rows:
        return
    tokens = sum(r["tokens"] for r in rows)
    events = sum(r["events"] for r in rows)
    tools = ", ".join(r["tool"] for r in rows[:3])
    if len(rows) > 3:
        tools += f", +{len(rows) - 3} more"
    console.print(
        f"  [dim]Not counted above:[/dim] [green]{tokens:,}[/green] tokens dropped past "
        f"the response budget by {events:,} MCP call(s) ([dim]{tools}[/dim]) - tools with "
        "no counterfactual estimator yet, so only the truncation is measurable."
    )


def _print_forgone_read_skeleton_line(start: Path, db_path: Path, since_ts: float | None) -> None:
    """What each replacing surface would have saved, for repos that have it off.

    Read out of its own table rather than the savings ledger, and printed
    below the total rather than inside it, because none of it happened.

    The caveat is not decoration. This number is exactly half the question:
    it says what the replacement would have taken off the bill, and it cannot
    say what the agent would then have had to read back, because nothing was
    replaced and so nothing was recovered. A repo can show a large figure here
    and still be one where serving skeletons is a bad trade.
    """
    import sqlite3

    from repowise.cli.helpers import find_repowise_repo_root

    repo_root = find_repowise_repo_root(start) or start
    printed = False
    for source, flag, label, noun, install_cmd in _FORGONE_SURFACES:
        try:
            con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2)
            try:
                where, params = " WHERE source = ?", (source,)
                if since_ts is not None:
                    where, params = " WHERE source = ? AND created_at >= ?", (source, since_ts)
                items, raw, distilled = con.execute(
                    "SELECT COUNT(DISTINCT path), COALESCE(SUM(raw_tokens),0), "
                    f"COALESCE(SUM(distilled_tokens),0) FROM forgone_savings{where}",
                    params,
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return  # no such table: this repo never measured, which is not an error
        if not items:
            continue

        # Rows outlive the setting that produced them, and nothing prunes them,
        # so the state has to be read rather than inferred from their presence,
        # or a repo that measured for a week and then turned the feature on gets
        # told forever that it is off and should turn it on.
        from repowise.cli.commands.augment_cmd._shared import hook_flag_enabled

        printed = True
        plural = "" if items == 1 else "s"
        if not hook_flag_enabled(repo_root, flag):
            console.print(
                f"  [dim]Not saved:[/dim] {label} are [yellow]off[/yellow] here: "
                f"{items:,} {noun}{plural} would have cost "
                f"[bold]{raw - distilled:,}[/bold] fewer tokens ({raw:,} → {distilled:,}). "
                f"[dim]Turn on with `{install_cmd}`.[/dim]"
            )
        else:
            console.print(
                f"  [dim]Measured before {label} was turned on:[/dim] {items:,} "
                f"{noun}{plural} would have cost "
                f"[bold]{raw - distilled:,}[/bold] fewer tokens ({raw:,} → {distilled:,}). "
                "[dim]Savings since then are in the table above.[/dim]"
            )
    if printed:
        console.print(
            "  [dim]This is what the replacement would have taken off the bill, and only "
            "that: nothing was replaced, so nothing was read back, so it says nothing "
            "about how often the agent would have needed the whole thing anyway.[/dim]"
        )


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
        _render_missed_distill_table(missed, days, pricing_model, start)
    if has_reread:
        _render_reread_table(reread, days, pricing_model)


def _render_missed_distill_table(
    report: dict, days: float, pricing_model: str, repo_root: Path | None = None
) -> None:
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
    console.print(f"  [dim]{_missed_tip(repo_root)}[/dim]")
    console.print()


def _resolve_pricing(repo_root: Path, override: str | None) -> tuple[str, str]:
    """The model to price saved tokens at, and how it was arrived at.

    The Costs endpoint has always priced this ledger at the model detected from
    the repo's most recent agent session; this command assumed Sonnet. Same
    tokens, two dollar figures, on two surfaces a reader takes for one — and
    the assumed one understates an Opus session by two thirds. Detection is
    shared with the endpoint rather than reimplemented, and any failure lands
    on the documented default rather than an error.
    """
    if override:
        return override, override
    try:
        from repowise.core.distill.session_model import resolve_session_model

        resolved = resolve_session_model(repo_root)
    except Exception:
        return DEFAULT_PRICING_MODEL, f"{DEFAULT_PRICING_MODEL}, assumed"
    return resolved.model, f"{resolved.model}, {resolved.source}"


def _rewrite_hook_installed() -> bool:
    """True when any agent surface has a rewrite hook that can actually fire.

    Mirrors the doctor check: Claude Code is always considered, Codex only
    when it is actually present on the machine. Registered is not enough — an
    entry whose matcher names a renamed tool fires on nothing, and telling
    someone their hook is installed is the one answer that hides why the rows
    below it are still there.
    """
    try:
        from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter
        from repowise.cli.agent_adapters.codex import CodexAdapter

        def live(adapter) -> bool:
            status = adapter.rewrite_hook_status()
            return status.installed and not status.unmatched

        if live(ClaudeCodeAdapter()):
            return True
        codex = CodexAdapter()
        return codex.detect() and live(codex)
    except Exception:
        return False


def _distill_opted_out(repo_root: Path | None) -> bool:
    """True when this repo turned the command path off in its own config."""
    if repo_root is None:
        return False
    try:
        from repowise.core.repo_config import load_repo_config

        cfg = load_repo_config(repo_root).get("distill")
        if not isinstance(cfg, dict):
            return False
        if cfg.get("enabled") is False:
            return True
        commands = cfg.get("commands")
        return isinstance(commands, dict) and commands.get("enabled") is False
    except Exception:
        return False


def _missed_tip(repo_root: Path | None = None) -> str:
    """The one next step that is actually true for this machine.

    Telling someone to install a hook they already installed hides the real
    reason the rows are still there: the hook only rewrites a single
    recognized command, so chained (``a && b``) and piped commands pass
    through by design and have to be distilled explicitly. A repo that opted
    out has a third, different reason again.
    """
    if not _rewrite_hook_installed():
        return (
            "Tip: install the rewrite hook ('repowise hook rewrite install') "
            "to catch these automatically."
        )
    if _distill_opted_out(repo_root):
        return (
            "Tip: the rewrite hook is installed but this repo opted out "
            "(distill.commands.enabled: false in .repowise/config.yaml), so nothing "
            "here was rewritten. Set it back to true to catch these automatically."
        )
    return (
        "Tip: the hook is installed. What is left is mostly commands it will not "
        "rewrite - it only wraps a single recognized command, so chained and piped "
        "ones pass through. Run those through 'repowise distill <cmd>' yourself."
    )


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
