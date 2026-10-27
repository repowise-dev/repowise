"""Output for a scored change: the table, and the json / markdown / github formats."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.markup import escape
from rich.table import Table

from repowise.cli.ci import append_step_summary, write_gate_summary
from repowise.cli.helpers import console, repo_index_session, run_async
from repowise.core.analysis.change_risk import ChangeRiskResult, change_risk_payload
from repowise.core.analysis.change_risk.render import LIMITS, fix_history_text, size_text

from .change import _Subject

#: How many ungrouped names the table prints; the summary already states the count.
_UNGROUPED_SHOWN = 10


def _independent_changes_for(repo_path: str, result, revspec: str | None) -> dict | None:
    """How this diff splits, per the index, or ``None`` when it cannot be read."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core import git_refs
    from repowise.core.analysis.independent_changes import independent_changes

    paths = [path for path, _ in result.features.file_churn]
    if len(paths) < 2:
        return None
    # A diff names paths from the repository root, so a command run in a
    # subdirectory has to score against that root, not against the given path.
    root = Path(git_refs.toplevel(repo_path) or repo_path).resolve()
    # Read git before the store opens: no subprocess while a connection is held.
    sets = git_refs.commit_file_sets(str(root), revspec)

    async def read() -> dict | None:
        found = None
        async with repo_index_session(root) as opened:
            if opened is not None:
                session, repo_id = opened
                try:
                    found = await independent_changes(session, repo_id, paths, commit_sets=sets)
                except (SQLAlchemyError, OSError, LookupError):
                    # An index written by an older version can fail the query itself.
                    return None
        return found.to_dict() if found else None

    return run_async(read())


def _print_independent_changes(block: dict) -> None:
    """The groups, plainly: no score, no percentage, no adjective."""
    console.print(f"\n[bold]{escape(str(block.get('summary') or ''))}[/bold]")
    for number, group in enumerate(block.get("groups") or [], start=1):
        files = [str(f) for f in group.get("files") or []]
        plural = "" if len(files) == 1 else "s"
        console.print(f"  {number}. {len(files)} file{plural}: {escape(', '.join(files))}")
        bridging = [str(f) for f in group.get("bridging_files") or []]
        if bridging:
            # Naming these files says where the group would split if one moved out.
            console.print(f"     [dim]held together by: {escape(', '.join(bridging))}[/dim]")
    ungrouped = [str(f) for f in block.get("ungrouped_files") or []]
    if ungrouped:
        shown = escape(", ".join(ungrouped[:_UNGROUPED_SHOWN]))
        cut = len(ungrouped) - _UNGROUPED_SHOWN
        more = f" and {cut} more" if cut > 0 else ""
        console.print(f"  Left out of the grouping: {shown}{more}")
    console.print(f"  [dim]Basis: {escape(str(block.get('basis') or ''))}.[/dim]")


def _print_change(subject: _Subject, result: ChangeRiskResult, failed: bool) -> None:
    """The table rendering of a scored change."""
    _print_size(result, subject.fail_above, failed)
    _print_fix_history_and_shape(result)
    groups = _independent_changes_for(subject.repo_path, result, subject.revspec)
    if groups:
        _print_independent_changes(groups)


def _print_size(result: ChangeRiskResult, fail_above: float | None, failed: bool) -> None:
    """Lead with the one percentile: the change's rank by diff size and spread.

    Without a usable baseline, label the offline absolute band explicitly as
    the fallback.
    """
    console.print(f"\n[bold]{size_text(result.percentile, fail_above)}[/bold]")
    if result.percentile is None:
        level = result.risk.level
        color = {"high": "red", "moderate": "yellow", "low": "green"}[level]
        console.print(f"  Absolute per-commit diff-size band: [{color}]{level}[/{color}]")
    elif fail_above is not None:
        from repowise.core.analysis.change_risk.render import gate_text

        color = "red" if failed else "green"
        console.print(f"  [{color}]Gate: {gate_text(result, fail_above)}.[/{color}]")


def _print_fix_history_and_shape(result: ChangeRiskResult) -> None:
    """Where the change lands (its fix record), then the shape of its diff."""
    _print_fix_headline(result)
    _print_change_facts(result)
    _print_hot_files(result)
    _print_diff_shape(result)


def _print_fix_headline(result: ChangeRiskResult) -> None:
    """Fix history remains separate evidence about where the change lands."""
    color = "red" if result.hot_files else "green"
    if not result.fix_history_available:
        color = "yellow"
    console.print(
        f"\n[bold]Bug-fix record[/bold] for [cyan]{escape(result.features.ref)}[/cyan]: "
        f"[{color}]{fix_history_text(result)}[/{color}]"
    )


def _print_change_facts(result: ChangeRiskResult) -> None:
    """What was scored: the tree, the subject, the filters and the diff's features."""
    features, request_excludes = result.features, result.request_excludes
    if result.working_tree:
        console.print("  [dim]Scoring your uncommitted changes, not the last commit.[/dim]")
    # A range's subject is its head commit's; its fix flag covers every commit.
    is_range = ".." in features.ref
    if features.subject:
        head = "Head commit: " if is_range else ""
        console.print(f"  [dim]{head}{features.subject}[/dim]")
    if request_excludes:
        console.print(f"  [dim]Excluding {len(request_excludes)} pattern(s).[/dim]")
    console.print(
        f"  +{features.la} / -{features.ld} lines · {features.nf} files · "
        f"{features.nd} dirs · {features.ns} subsystems · "
        f"entropy {features.entropy:.2f} · author exp "
        f"{'unknown' if features.exp is None else features.exp}"
        + (
            f"  [magenta]({'range contains a fix' if is_range else 'fix'})[/magenta]"
            if features.is_fix
            else ""
        )
    )


def _print_hot_files(result: ChangeRiskResult) -> None:
    """The fix record, named in the headline above and itemized here."""
    if result.hot_files:
        hot = Table(show_header=True)
        hot.add_column("File")
        hot.add_column("Lines", justify="right")
        hot.add_column("Prior fixes", justify="right")
        for path, churn, pressure in result.hot_files:
            hot.add_row(escape(path), str(churn), f"{pressure:.1f}")
        console.print(hot)
        console.print(
            "  [dim]Prior fixes are recency-weighted against this change's own date — "
            "one from a year earlier counts a half.[/dim]"
        )


def _print_diff_shape(result: ChangeRiskResult) -> None:
    """The supporting 0-10 diff-size score and its drivers."""
    risk = result.risk
    console.print(
        f"  [dim]Diff-size score: {risk.score:.1f}/10 — how big and spread out the change is, "
        f"not where it lands. Corpus-anchored to a single commit.[/dim]"
    )
    console.print(f"  [dim]{LIMITS}[/dim]")

    table = Table(title="Diff shape (each driver vs. the model's baseline commit)")
    table.add_column("Driver")
    table.add_column("Value", justify="right")
    table.add_column("Push", justify="right")
    for d in risk.top_drivers:
        push_color = "red" if d.contribution > 0 else "green"
        sign = "+" if d.contribution >= 0 else ""
        table.add_row(
            d.label,
            "-" if d.value is None else f"{d.value:g}",
            f"[{push_color}]{sign}{d.contribution:.2f}[/{push_color}]",
        )
    console.print(table)


def _emit_machine(subject: _Subject, result: ChangeRiskResult) -> None:
    """The json, markdown and github renderings of a scored change."""
    from repowise.core.analysis.change_risk import render

    fmt, fail_above = subject.fmt, subject.fail_above
    if fmt == "json":
        payload = change_risk_payload(result, scales=True)
        groups = _independent_changes_for(subject.repo_path, result, subject.revspec)
        if groups:
            payload["independent_changes"] = groups
        if fail_above is not None:
            payload["gate"] = {
                "fail_above_percentile": fail_above,
                # Unrounded, as the gate compares it: ``risk_percentile`` is
                # rounded to one decimal and can read as equal to the gate.
                "percentile": result.percentile,
                "status": render.percentile_gate(result, fail_above),
            }
        click.echo(json.dumps(payload, indent=2))
    elif fmt == "markdown":
        click.echo(render.render_markdown(result, fail_above), nl=False)
    else:
        for line in render.github_annotations(result, fail_above):
            click.echo(line)
        append_step_summary(render.render_markdown(result, fail_above))
        write_gate_summary(render.gate_summary(result, fail_above))
        console.print(render.headline(result, fail_above, markdown=False), markup=False)
