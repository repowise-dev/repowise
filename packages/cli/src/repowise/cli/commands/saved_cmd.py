"""``repowise saved`` — report the input tokens agents did not have to read.

Reads the canonical savings ledger in the omissions sidecar
(``.repowise/omissions/omissions.db``) through the one core report service, so
this command, the savings endpoint and the repository overview headline report
the same figures by construction. They previously did not: each aggregated and
priced the ledger for itself, and the three disagreed about which rows counted
as distillation, whether MCP tokens entered the dollar figure, whether a fixed
output credit was added, and what window applied.

The ledger covers the ``repowise distill`` path, the hooks that replace a tool
result, and MCP calls. ``--by surface`` splits them.

Named ``saved`` rather than ``distill --stats`` because ``repowise distill``
captures everything after it as the command to run (``ignore_unknown_options``)
— a ``--stats`` flag there would be indistinguishable from a command named
``--stats``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.table import Table

if TYPE_CHECKING:
    from repowise.core.savings.contracts import SavingsReport

from repowise.cli.helpers import console
from repowise.cli.output import emit_json, format_option, notice_console

#: Fallback pricing model for the dollar estimate. Saved tokens are input-side
#: tokens the coding agent never had to read, so the input rate applies. Used
#: only when no agent session can be detected — see :func:`_resolve_pricing`.
DEFAULT_PRICING_MODEL = "claude-sonnet-4-6"


@click.command("saved")
@click.argument("path", required=False, default=None)
@click.option(
    "--by",
    "group_by",
    type=click.Choice(["operation", "surface", "agent", "model", "day"]),
    default="operation",
    show_default=True,
    help="Group savings by operation, surface, agent, pricing model, or day.",
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
        "Pricing model for the opportunity estimates under --missed. Recorded "
        "savings are priced at the rate captured on each event, so this does "
        "not affect them. Defaults to the model detected from this repo's most "
        f"recent agent session, falling back to {DEFAULT_PRICING_MODEL}."
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
@format_option()
def saved_command(
    path: str | None,
    group_by: str,
    since: str | None,
    pricing_model: str | None,
    show_missed: bool,
    missed_days: float,
    fmt: str,
) -> None:
    """Show the input tokens agents did not have to read, and what they were worth.

    PATH defaults to the current directory. The report covers that repository's
    savings ledger; a directory with no ``.repowise/`` has none, which is
    reported as "nothing recorded" rather than as zero. Covers the distill
    path, the replacement hooks and MCP calls; ``--by surface`` splits them.
    """
    from repowise.core.distill.store import default_store_path
    from repowise.core.savings.service import load_report

    notices = notice_console(fmt)
    since_ts = _parse_since(since)
    window_days = _window_days(since_ts)

    start = Path(path).resolve() if path else Path.cwd()

    if show_missed:
        # Resolved only on this branch. Detection scans the local agent
        # transcripts -- seconds, on a repository with no Codex history -- and
        # only the opportunity estimates need it. Recorded savings carry the
        # rate they were written with, so the default report must never pay for
        # a detection it does not use.
        pricing_model, _ = _resolve_pricing(start, pricing_model)
        if fmt == "json":
            emit_json(
                {
                    "days": missed_days,
                    "pricing_model": pricing_model,
                    "missed_distill": _missed_report(start, missed_days),
                    "missed_mcp_rereads": _reread_report(start, missed_days),
                }
            )
            return
        _print_missed_report(start, missed_days, pricing_model)
        return

    db_path = default_store_path(start)
    report = load_report(start, days=window_days)
    if report is None:
        notices.print(
            "[yellow]No savings recorded yet.[/yellow] Run commands through "
            "'repowise distill <cmd>' (or install the rewrite hook with "
            "'repowise hook rewrite install') to start saving tokens."
        )
        if fmt == "json":
            emit_json({"ledger": str(db_path), "events": 0, "rows": []})
        return

    rows = _breakdown_rows(report, group_by)
    saved = report.saved_input_tokens

    if fmt == "json":
        emit_json(
            {
                "ledger": str(db_path),
                "group_by": group_by,
                "since": since,
                "window_days": window_days,
                "summary": {
                    "events": report.unique_events,
                    "saved_input_tokens": saved,
                    "measured_saved_input_tokens": report.measured_saved_input_tokens,
                    "inferred_saved_input_tokens": report.inferred_saved_input_tokens,
                    "priced_saved_input_tokens": report.priced_saved_input_tokens,
                    "unpriced_saved_input_tokens": report.unpriced_saved_input_tokens,
                    "estimated_usd": report.priced_input_savings_usd,
                    "mcp_queries_answered": report.mcp_queries_answered,
                    "dead_ends": report.dead_ends,
                    "baseline_events": report.baseline_events,
                    "reducing_events": report.reducing_events,
                    "baseline_input_tokens": report.baseline_input_tokens,
                    "baseline_saved_input_tokens": report.baseline_saved_input_tokens,
                    "input_reduction_ratio": report.input_reduction_ratio,
                    "input_reduction_ratio_p90": report.input_reduction_ratio_p90,
                    "first_event_at": report.first_event_at,
                    "last_event_at": report.last_event_at,
                },
                "rows": rows,
                "opportunities": {
                    "count": report.opportunity_count,
                    "estimated_potential_input_tokens": report.opportunity_tokens_excluded,
                    "per_kind": [dict(row) for row in report.per_opportunity_kind],
                },
                "net": _net_data(start, saved, since_ts),
                "missed_distill": _missed_report(start, missed_days),
                "missed_mcp_rereads": _reread_report(start, missed_days),
                "forgone": _forgone_rows(start, db_path, since_ts),
            }
        )
        _warm_pricing_cache(start, report)
        return

    if report.unique_events == 0:
        msg = "No savings events recorded"
        if since_ts is not None:
            msg += f" since {since}"
        console.print(f"[yellow]{msg}.[/yellow]")
        # Before returning, not after. Declining the init prompt turns off
        # distill rewrites *and* skeleton-served Reads in the same write, so
        # the cohort the counterfactual exists to inform is exactly the cohort
        # with zero distillation events -- and returning here first would make
        # its rows unreadable by the only command that reports them.
        _print_forgone_read_skeleton_line(start, db_path, since_ts)
        return

    table = Table(
        title=f"Savings - grouped by {group_by}",
        border_style="dim",
        show_footer=True,
        caption=(
            "Input tokens agents did not have to read, from the canonical "
            "savings ledger: the 'repowise distill' command path, the hooks "
            "that replace a tool result, and MCP calls. Group by surface, "
            "agent, model or day to split them."
        ),
    )
    table.add_column(group_by.capitalize(), style="cyan", footer="[bold]TOTAL[/bold]")
    table.add_column("Events", justify="right", footer=str(report.unique_events))
    table.add_column(
        "Saved Tokens", justify="right", footer=f"[bold green]{saved:,}[/bold green]"
    )
    for row in rows:
        table.add_row(
            str(row["group"] or "-"),
            str(row["events"]),
            f"[green]{row['saved_input_tokens']:,}[/green]",
        )

    console.print()
    console.print(table)
    _print_evidence_and_pricing(report)
    console.print(f"  [dim]Ledger: {db_path}[/dim]")
    _print_net(start, saved, since_ts)
    _print_missed_summary_line(start, missed_days)
    _print_reread_summary_line(start, missed_days)
    _print_forgone_read_skeleton_line(start, db_path, since_ts)
    console.print()
    _warm_pricing_cache(start, report)


def _warm_pricing_cache(start: Path, report: SavingsReport) -> None:
    """Resolve the pricing snapshot so future events can be priced.

    The surfaces that write events all run inside an agent's tool call and read
    this cache without ever filling it, so something has to. This command is
    the right place: a human typed it and is reading a dollar figure.

    Run only when the report actually shows unpriced savings, and only after
    the output is on screen. Resolving costs seconds on a cold cache, and it is
    self-limiting: once the cache is warm the events it prices stop being
    unpriced, so this stops firing.
    """
    if not report.unpriced_saved_input_tokens:
        return
    from repowise.core.savings.pricing import resolve_pricing_snapshot

    resolve_pricing_snapshot(start)


#: Which report breakdown each ``--by`` value reads, and the key its rows use
#: for the group. One mapping, so adding a breakdown is one line here rather
#: than a new branch in the table renderer and another in the JSON writer.
_BREAKDOWNS = {
    "operation": ("per_operation", "operation"),
    "surface": ("per_surface", "surface"),
    "agent": ("per_agent", "agent"),
    "model": ("per_model", "model"),
    "day": ("per_day", "day"),
}


def _breakdown_rows(report: SavingsReport, group_by: str) -> list[dict]:
    """One breakdown as uniform ``group``/``events``/``saved`` rows.

    Agents are labelled from the identity registry, which the report already
    carried through, so this never spells an agent name itself.
    """
    attribute, key = _BREAKDOWNS[group_by]
    rows = getattr(report, attribute)
    return [
        {
            "group": row.get("agent_display_name") or row[key] if key == "agent" else row[key],
            "events": row["events"],
            "saved_input_tokens": row["saved_input_tokens"],
        }
        for row in rows
    ]


def _print_evidence_and_pricing(report: SavingsReport) -> None:
    """The two splits that stop the total reading as one confident number.

    Measured against inferred says how the saving was established; priced
    against unpriced says how much of it carries a rate at all. Both are
    reported rather than folded in, because an event is priced at the rate
    captured when it happened and some events carry none -- pricing those at
    today's rate would be inventing evidence.
    """
    console.print(
        f"  Measured: [bold]{report.measured_saved_input_tokens:,}[/bold]  "
        f"Inferred: [bold]{report.inferred_saved_input_tokens:,}[/bold]"
    )
    # Out of how much, not just how much. Both the aggregate and the peak,
    # because either alone misreads in whichever direction flatters.
    if report.input_reduction_ratio is not None and report.input_reduction_ratio_p90 is not None:
        console.print(
            f"  Input reduction: [bold]{report.input_reduction_ratio:.0%}[/bold] "
            f"[dim]on {report.reducing_events:,} of {report.baseline_events:,} "
            f"comparable interactions; "
            f"{report.input_reduction_ratio_p90:.0%} at the 90th percentile[/dim]"
        )
    if report.priced_saved_input_tokens:
        console.print(
            f"  Estimated saved: [bold green]${report.priced_input_savings_usd:.4f}[/bold green] "
            f"[dim](on {report.priced_saved_input_tokens:,} priced tokens; "
            f"tokens are chars/4 estimates)[/dim]"
        )
    if report.unpriced_saved_input_tokens:
        console.print(
            f"  [dim]{report.unpriced_saved_input_tokens:,} tokens carry no recorded "
            f"rate and are not priced.[/dim]"
        )


def _net_data(start: Path, saved_tokens: int, since_ts: float | None) -> dict | None:
    """The net figures behind :func:`_print_net`, or ``None`` when there is no net.

    Split out so ``--format json`` reports the same numbers the table path
    prints rather than a second, drifting computation of them. The three
    honesty rules documented on :func:`_print_net` all live here, since they
    decide whether a net exists at all: a windowed ``--since`` has no
    comparable debit side, and neither does a repo with no debit rows.
    """
    if since_ts is not None:
        return None
    try:
        from repowise.cli.helpers import find_repowise_repo_root
        from repowise.core.sessions.efficacy import advisory_cost
        from repowise.core.sessions.footprint import measure
        from repowise.core.sessions.staging import SessionStagingStore, default_store_path

        repo_root = find_repowise_repo_root(start) or start
        advisory_chars = advisory_firings = 0
        if default_store_path(repo_root).exists():
            store = SessionStagingStore.open_default(repo_root)
            try:
                advisory_chars, advisory_firings = advisory_cost(store.efficacy_rows())
            finally:
                store.close()
        footprint = measure(
            repo_root,
            advisory_chars=advisory_chars,
            advisory_firings=advisory_firings,
        )
    except Exception:
        return None
    if not footprint.debits:
        return None

    amp = footprint.amplification
    billed_saved = int(saved_tokens * (amp.ratio if amp.known else 1.0))
    return {
        "billed_saved": billed_saved,
        "billed_spent": footprint.billed_total,
        "net": billed_saved - footprint.billed_total,
        "amplification": (
            {"known": True, "ratio": amp.ratio, "sessions": amp.sessions, "calls": amp.calls}
            if amp.known
            else {"known": False}
        ),
        "debits": [
            {
                "label": d.label,
                "billed_tokens": d.billed_tokens,
                "raw_tokens": d.raw_tokens,
                "detail": d.detail,
            }
            for d in footprint.debits
        ],
        "unmeasured": list(footprint.unmeasured),
    }


def _print_net(start: Path, saved_tokens: int, since_ts: float | None = None) -> None:
    """Gross saved, gross spent, net — and the net may be negative.

    This is the only line here that can answer "is this worth mounting". The
    table above counts credits and structurally cannot report a loss, which
    made every figure it printed an advertisement rather than a measurement.

    Three honesty rules are load-bearing.

    **The two sides have to cover the same window.** The debit side cannot be
    windowed: the resident prefix is a property of the file as it is now, not
    of any date range. So under ``--since`` this prints nothing at all rather
    than setting a windowed credit against an all-time cost, which produced a
    confident negative that was an artifact of the window and nothing else.

    **Sessions that predate the debit ledger have no cost rows**, so a net
    across them credits savings whose cost was never recorded.

    **The debit total is a lower bound**: some real costs are not computable
    from local data. They are named rather than dropped, so the net reads as
    "no better than this".
    """
    if since_ts is not None:
        console.print(
            "\n  [dim]No net under --since: savings can be windowed by date and the "
            "resident cost of the CLAUDE.md block cannot, so the two would not "
            "describe the same period. Run without --since for the net.[/dim]"
        )
        return
    data = _net_data(start, saved_tokens, since_ts)
    if data is None:
        return

    net = data["net"]
    colour = "green" if net > 0 else "red"
    amp = data["amplification"]

    console.print()
    console.print("  [bold]Net[/bold] [dim](billed tokens, after amplification)[/dim]")
    console.print(f"    gross saved   [green]{data['billed_saved']:>12,}[/green]")
    console.print(f"    gross spent   [yellow]{data['billed_spent']:>12,}[/yellow]")
    console.print(f"    net           [{colour}]{net:>12,}[/{colour}]")
    for debit in data["debits"]:
        console.print(
            f"      [dim]{debit['label']}: {debit['billed_tokens']:,} "
            f"({debit['raw_tokens']:,} raw — {debit['detail']})[/dim]"
        )
    if amp["known"]:
        console.print(
            f"      [dim]amplification {amp['ratio']:.1f}x, measured over {amp['sessions']} "
            f"sessions at a median {amp['calls']} API calls each. It is a function of "
            "session length, not a constant.[/dim]"
        )
    else:
        console.print(
            "      [dim]no cache figures on disk, so nothing was amplified and "
            "both sides are raw tokens.[/dim]"
        )
    for missing in data["unmeasured"]:
        console.print(f"      [dim]not counted as a cost: {missing}.[/dim]")
    console.print(
        "      [dim]Savings recorded before the cost side existed have no debit "
        "rows behind them, so this net is a ceiling on how good the trade is, "
        "never a floor.[/dim]"
    )


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
    (
        "hook-read",
        "read_reread",
        "collapsed re-reads",
        "re-read",
        "repowise hook read-reread install",
    ),
)


def _forgone_rows(start: Path, db_path: Path, since_ts: float | None) -> list[dict]:
    """One row per replacing surface that has measured rows, else ``[]``.

    A surface with no rows is omitted rather than reported as zero: nothing
    was measured there, which is a different claim from "would have saved
    nothing".
    """
    import sqlite3

    from repowise.cli.commands.augment_cmd._shared import hook_flag_enabled
    from repowise.cli.helpers import find_repowise_repo_root

    repo_root = find_repowise_repo_root(start) or start
    rows: list[dict] = []
    for source, flag, label, noun, install_cmd in _FORGONE_SURFACES:
        try:
            con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2)
            try:
                # Filtered on the surface's *filter*, not its source: the
                # skeleton and the re-read collapse share ``hook-read``, so a
                # source-only sum would report each one's counterfactual twice.
                # Rows predating the ``filter`` column carry '' and land under
                # the surface that was the only one writing when they were made.
                legacy = " OR (filter = '' AND source = ?)" if flag == "read_skeleton" else ""
                params: tuple = (flag, source) if legacy else (flag,)
                where = f" WHERE (filter = ?{legacy})"
                if since_ts is not None:
                    where += " AND created_at >= ?"
                    params = (*params, since_ts)
                items, raw, distilled = con.execute(
                    "SELECT COUNT(DISTINCT path), COALESCE(SUM(raw_tokens),0), "
                    f"COALESCE(SUM(distilled_tokens),0) FROM forgone_savings{where}",
                    params,
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            # ``break``, not ``return []``: the surfaces already read are real
            # measurements and the printer used to keep them, because it
            # printed as it went. The common error (no such table) still fails
            # on the first surface and yields nothing either way; a locked
            # database part-way through must not retract what was gathered.
            break
        if not items:
            continue
        rows.append(
            {
                "filter": flag,
                "source": source,
                "label": label,
                "noun": noun,
                "install_cmd": install_cmd,
                # Rows outlive the setting that produced them, and nothing
                # prunes them, so the state has to be read rather than inferred
                # from their presence, or a repo that measured for a week and
                # then turned the feature on gets told forever that it is off.
                "enabled": hook_flag_enabled(repo_root, flag),
                "items": items,
                "raw_tokens": raw,
                "distilled_tokens": distilled,
                "forgone_tokens": raw - distilled,
            }
        )
    return rows


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
    rows = _forgone_rows(start, db_path, since_ts)
    for row in rows:
        items, raw, distilled = row["items"], row["raw_tokens"], row["distilled_tokens"]
        plural = "" if items == 1 else "s"
        if not row["enabled"]:
            console.print(
                f"  [dim]Not saved:[/dim] {row['label']} are [yellow]off[/yellow] here: "
                f"{items:,} {row['noun']}{plural} would have cost "
                f"[bold]{raw - distilled:,}[/bold] fewer tokens ({raw:,} → {distilled:,}). "
                f"[dim]Turn on with `{row['install_cmd']}`.[/dim]"
            )
        else:
            console.print(
                f"  [dim]Measured before {row['label']} was turned on:[/dim] {items:,} "
                f"{row['noun']}{plural} would have cost "
                f"[bold]{raw - distilled:,}[/bold] fewer tokens ({raw:,} → {distilled:,}). "
                "[dim]Savings since then are in the table above.[/dim]"
            )
    if rows:
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
    # OSError, not just ValueError: on Windows a pre-epoch date parses fine and
    # then raises errno 22 from timestamp(), which would be a traceback rather
    # than a usage error.
    except (ValueError, OSError, OverflowError) as exc:
        raise click.BadParameter(f"Cannot parse date '{value}': {exc}") from exc


def _estimate_usd(saved_tokens: int, model: str) -> tuple[float, float]:
    """Dollar estimate for *saved_tokens* at *model*'s input rate.

    Used only for the ``--missed`` opportunity estimates, which are hypothetical
    tokens with no event and therefore no captured rate. Recorded savings are
    never priced this way: each event carries the rate it was worth when it
    happened, so repricing history at today's model would move past figures.
    """
    from repowise.core.generation.cost_tracker import get_model_pricing

    rate = get_model_pricing(model)["input"]
    return saved_tokens * rate / 1_000_000, rate


def _window_days(since_ts: float | None) -> int | None:
    """``--since`` as a whole-day window, or ``None`` for all time.

    The report windows by days back from now, so a date becomes the number of
    days that covers it. Rounded up, because a window that excluded the day the
    user named would report less than they asked for.
    """
    if since_ts is None:
        return None
    import math
    import time

    elapsed = time.time() - since_ts
    return max(0, math.ceil(elapsed / 86_400))
