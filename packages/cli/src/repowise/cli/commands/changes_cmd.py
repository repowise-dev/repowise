"""``repowise changes`` - the merged changes reconstructed from git.

``list`` pages the indexed changes newest first; ``show`` prints one with its
evidence checklist, as the native versioned JSON record or, with
``--format in-toto``, as an in-toto Statement v1; ``export`` writes every
change merged in a window with its checklist, as JSON Lines or CSV. All read the
index; ``repowise init`` or ``update`` fills it. A checklist is built on first
read (seconds per change) and kept on the change until ``--refresh``; its
``outcomes`` item (reverts, later fixes naming the change, the files' later
fix rate) is read again whenever HEAD has moved.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from repowise.cli.helpers import (
    console,
    repo_index_session,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output_until_close,
)
from repowise.cli.output import emit_json, format_option

if TYPE_CHECKING:
    from repowise.core.persistence.crud import ChangeFilter


def _repo_root(path: str | None) -> Path:
    target = resolve_command_target(path=path, no_workspace_flag=True)
    assert target.repo_path is not None
    return Path(target.repo_path).resolve()


def _no_index() -> None:
    console.print("No index found. Run `repowise init` first.")
    raise SystemExit(1)


async def _list(
    root: Path, where: ChangeFilter, *, limit: int, offset: int, needs_attention: bool = False
) -> tuple[list[dict], int] | None:
    from repowise.core.attribution import attribution_enabled
    from repowise.core.changes.collect import list_needing_attention
    from repowise.core.changes.record import change_record
    from repowise.core.persistence.crud import list_changes

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        read = list_needing_attention if needs_attention else list_changes
        rows, total = await read(session, repo_id, where, limit=limit, offset=offset)
        on = attribution_enabled(root)
        return [change_record(r, attribution=on) for r in rows], total


async def _show(root: Path, ref: str, *, evidence: bool, refresh: bool) -> dict | None | bool:
    from repowise.core.attribution import attribution_enabled
    from repowise.core.changes.collect import change_evidence
    from repowise.core.changes.record import change_record
    from repowise.core.persistence.crud import get_change

    async with repo_index_session(root) as opened:
        if opened is None:
            return False
        session, repo_id = opened
        row = await get_change(session, repo_id, ref)
        if row is None:
            return None
        record = change_record(row, attribution=attribution_enabled(root))
        if evidence:
            got = await change_evidence(session, repo_id, root, row, refresh=refresh)
            record["evidence"] = got.to_dict()
        return record


def build_progress(position: int, total: int, change_id: str) -> None:
    """One stderr line per checklist built, so a long period shows it is moving."""
    click.echo(f"evidence {position}/{total}: {change_id}", err=True)


def build_plan(to_build: int, total: int) -> None:
    """Say up front how many checklists a period builds, and roughly how long."""
    from repowise.core.changes.period import SECONDS_PER_BUILD

    if not to_build:
        return
    minutes = to_build * SECONDS_PER_BUILD / 60
    about = f"about {minutes:.0f} min" if minutes >= 1 else "under a minute"
    click.echo(
        f"building {to_build} of {total} evidence checklist(s), {about}; each is kept, "
        "so a later run reads it back",
        err=True,
    )


async def _export(
    root: Path,
    since: datetime | None,
    until: datetime | None,
    refresh: bool,
    emit: Callable[[dict], None],
) -> int | None:
    """Emit each change in the window with its checklist, a page at a time; the count."""
    from repowise.core.attribution import attribution_enabled
    from repowise.core.changes.period import PeriodRequest, period_evidence
    from repowise.core.changes.record import change_record

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        on = attribution_enabled(root)
        done = 0
        # An export is the whole window it names; without --since, every indexed change.
        request = PeriodRequest(
            since=since,
            until=until,
            window_days=None,
            refresh=refresh,
            progress=build_progress,
            plan=build_plan,
        )
        async for row, evidence in period_evidence(session, repo_id, root, request):
            record = change_record(row, attribution=on)
            record["evidence"] = evidence.to_dict()
            emit(record)
            done += 1
        return done


_STATUS_STYLE = {"ok": "green", "attention": "yellow", "unknown": "dim"}


def _print_checklist(evidence: dict) -> None:
    from rich.markup import escape
    from rich.table import Table

    from repowise.core.changes.evidence import BASIS_TEXT, item_line, line_text

    table = Table("Evidence", "Status", "Basis", "Value", title="Evidence checklist")
    for item in evidence["items"]:
        style = _STATUS_STYLE.get(item["status"], "")
        table.add_row(
            escape(item["label"]),
            f"[{style}]{item['status']}[/{style}]",
            BASIS_TEXT.get(item["basis"], item["basis"]),
            escape(line_text(item_line(item))),
        )
    console.print(table)
    console.print(f"[dim]generated {evidence['generated_at']}; `--refresh` rebuilds it.[/dim]")
    outcomes = next((i for i in evidence["items"] if i["kind"] == "outcomes"), None)
    if outcomes is not None and outcomes["status"] != "unknown":
        console.print("[bold]Outcomes[/bold]")
        for line in outcome_lines(outcomes):
            console.print(f"  {escape(line)}")


def outcome_lines(item: dict) -> list[str]:
    """The outcomes item in words: links quote their message, the rate states its basis."""
    lines = []
    for row in item["details"]:
        later = row.get("sha", "")[:12]
        if row.get("change_id"):
            later = f"{row['change_id']} ({later})"
        if row["kind"] == "revert":
            held = "" if row.get("in_force") else " (no longer in force)"
            lines.append(f"reverted by {later}{held}: {row['evidence']}")
        elif row["kind"] == "fix_reference":
            lines.append(
                f"referenced as the cause by a later fix, {later} (their words): \"{row['evidence']}\""
            )
        elif row["kind"] == "file_fix_rate":
            open_ = "" if row["window_complete"] else " (window still open)"
            lines.append(
                f"files later received fixes: {row['files_fixed']} of {row['files']}"
                f" within {row['days']} days{open_}"
            )
    if not any(r["kind"] in ("revert", "fix_reference") for r in item["details"]):
        lines.insert(0, "no later commit in this history reverts this change or names it as a cause")
    return lines


_CSV_COLUMNS = (
    "change_id",
    "merged_at",
    "title",
    "kind",
    "label",
    "status",
    "basis",
    "value",
    "source",
)


def _csv_rows(record: dict) -> str:
    """One CSV row per evidence item of *record*; ``value`` is its JSON."""
    import csv
    import io
    import json

    from repowise.core.report.render import spreadsheet_cell

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for item in record["evidence"]["items"]:
        writer.writerow(
            spreadsheet_cell(cell)
            for cell in [
                record["id"],
                record["merged_at"] or "",
                record["title"],
                item["kind"],
                item["label"],
                item["status"],
                item["basis"],
                json.dumps(item["value"], sort_keys=True),
                item["source"],
            ]
        )
    return buf.getvalue()


@click.group("changes")
def changes_group() -> None:
    """List, show and export merged changes (PRs, MRs, commits) reconstructed from git."""


@changes_group.command("list")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@click.option("--category", default=None, help="Only this category (feature, fix, ...).")
@click.option(
    "--shape", type=click.Choice(["merge", "squash", "commit"]), default=None, help="Only this shape."
)
@click.option("--grep", "q", default=None, help="Substring of the title, id or a file path.")
@click.option(
    "--needs-attention",
    is_flag=True,
    help="Only changes whose kept checklist flags attention (never builds one).",
)
@click.option("--limit", default=30, show_default=True, type=click.IntRange(1, 1000))
@click.option("--offset", default=0, type=click.IntRange(0))
@format_option()
def changes_list(
    repo: str | None,
    category: str | None,
    shape: str | None,
    q: str | None,
    needs_attention: bool,
    limit: int,
    offset: int,
    fmt: str,
) -> None:
    """List indexed changes, newest merged first."""
    from repowise.core.persistence.crud import ChangeFilter

    root = _repo_root(repo)
    where = ChangeFilter(category=category, shape=shape, q=q)
    got = run_async(
        _list(root, where, limit=limit, offset=offset, needs_attention=needs_attention)
    )
    if got is None:
        _no_index()
    records, total = got
    if fmt == "json":
        for r in records:
            r.pop("files")
        emit_json({"total": total, "limit": limit, "offset": offset, "changes": records})
        return
    from rich.markup import escape
    from rich.table import Table

    table = Table("Change", "Merged", "Category", "Files", "+/-", "Title")
    for r in records:
        table.add_row(
            escape(r["forge_ref"] or r["id"][:19]),
            (r["merged_at"] or "")[:10],
            r["category"],
            str(r["files_changed"]),
            f"+{r['lines_added']}/-{r['lines_deleted']}",
            escape(r["title"][:80]),
        )
    console.print(table)
    console.print(f"[dim]{len(records)} of {total} change(s).[/dim]")


@changes_group.command("show")
@click.argument("ref")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@format_option(
    choices=("table", "json", "in-toto"),
    help="table, the native JSON record, or an in-toto Statement v1.",
)
@click.option(
    "--evidence/--no-evidence",
    default=True,
    show_default=True,
    help="Include the evidence checklist (built on first read, then kept).",
)
@click.option("--refresh", is_flag=True, help="Rebuild the evidence checklist.")
def changes_show(ref: str, repo: str | None, fmt: str, evidence: bool, refresh: bool) -> None:
    """Show one change by id (pr-123, change-I..., commit-<sha>) or by a commit sha."""
    root = _repo_root(repo)
    if evidence:
        # Building a checklist parses code, which logs; keep the table clean.
        silence_logs_for_machine_output_until_close()
    record = run_async(_show(root, ref, evidence=evidence, refresh=refresh))
    if record is False:
        _no_index()
    if record is None:
        console.print(f"No change {ref!r} in the index.")
        raise SystemExit(1)
    if fmt == "json":
        emit_json(record)
        return
    if fmt == "in-toto":
        from repowise.core.changes.record import in_toto_statement

        emit_json(in_toto_statement(record))
        return
    from rich.markup import escape

    ref_text = record["forge_ref"] or record["id"]
    console.print(f"[bold]{escape(ref_text)}[/bold]  {escape(record['title'])}")
    console.print(
        f"{record['shape']} · {record['category']} · merged {record['merged_at'] or 'unknown'}"
        f" · head {record['head_sha'][:12]}"
    )
    console.print(
        f"{len(record['commits'])} commit(s), {record['files_changed']} file(s),"
        f" +{record['lines_added']}/-{record['lines_deleted']}"
    )
    prov = record["provenance"]
    if prov is not None:
        cov = prov["coverage"]
        cov_text = f"{round(cov * 100)}%" if cov is not None else "n/a"
        console.print(f"provenance: {prov['label']} (coverage {cov_text})")
    meta = record["metadata"]
    if meta["linked_issues"]:
        console.print(f"linked issues: {', '.join(meta['linked_issues'])}")
    if meta["approvals"] is not None:
        console.print(f"approvals: {meta['approvals']}")
    for f in record["files"][:50]:
        console.print(f"  {escape(f['path'])}  +{f['added']}/-{f['deleted']}")
    if record["files_changed"] > 50:
        console.print(f"[dim]  ... {record['files_changed'] - 50} more[/dim]")
    if "evidence" in record:
        _print_checklist(record["evidence"])


@changes_group.command("export")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@click.option(
    "--since", type=click.DateTime(), default=None, help="Merged on or after this date (UTC)."
)
@click.option("--until", type=click.DateTime(), default=None, help="Merged before this date (UTC).")
@format_option(
    choices=("json", "csv"),
    default="json",
    help="json: one record per line (JSON Lines); csv: one row per change and item.",
)
@click.option("--refresh", is_flag=True, help="Rebuild every evidence checklist.")
def changes_export(
    repo: str | None, since: datetime | None, until: datetime | None, fmt: str, refresh: bool
) -> None:
    """Export the changes merged in a window, each with its evidence checklist.

    A change without a kept checklist has one built and kept first, a few at
    a time over one shared fix-history walk (about 3s per change measured on
    a mid-size repository, from 6s one by one); kept checklists are read back
    at no cost. Records stream a page at a time, and progress goes to stderr.
    """
    import json
    from datetime import UTC

    root = _repo_root(repo)
    since, until = (d.replace(tzinfo=UTC) if d else None for d in (since, until))
    if fmt == "csv":
        click.echo(",".join(_CSV_COLUMNS))

        def emit(record: dict) -> None:
            click.echo(_csv_rows(record), nl=False)
    else:

        def emit(record: dict) -> None:
            click.echo(json.dumps(record, default=str))

    if run_async(_export(root, since, until, refresh, emit)) is None:
        _no_index()
