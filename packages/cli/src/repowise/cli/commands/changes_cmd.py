"""``repowise changes`` - the merged changes reconstructed from git.

``list`` pages the indexed changes newest first; ``show`` prints one as the
native versioned JSON record or, with ``--format in-toto``, as an in-toto
Statement v1. Both read the index; ``repowise init`` or ``update`` fills it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import console, repo_index_session, resolve_command_target, run_async
from repowise.cli.output import emit_json, format_option


def _repo_root(path: str | None) -> Path:
    target = resolve_command_target(path=path, no_workspace_flag=True)
    assert target.repo_path is not None
    return Path(target.repo_path).resolve()


def _no_index() -> None:
    console.print("No index found. Run `repowise init` first.")
    raise SystemExit(1)


async def _list(root: Path, **filters: Any) -> tuple[list[dict], int] | None:
    from repowise.core.attribution import attribution_enabled
    from repowise.core.changes.record import change_record
    from repowise.core.persistence.crud import list_changes

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        rows, total = await list_changes(session, repo_id, **filters)
        on = attribution_enabled(root)
        return [change_record(r, attribution=on) for r in rows], total


async def _show(root: Path, ref: str) -> dict | None | bool:
    from repowise.core.attribution import attribution_enabled
    from repowise.core.changes.record import change_record
    from repowise.core.persistence.crud import get_change

    async with repo_index_session(root) as opened:
        if opened is None:
            return False
        session, repo_id = opened
        row = await get_change(session, repo_id, ref)
        return None if row is None else change_record(row, attribution=attribution_enabled(root))


@click.group("changes")
def changes_group() -> None:
    """List and show merged changes (PRs, MRs, commits) reconstructed from git."""


@changes_group.command("list")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@click.option("--category", default=None, help="Only this category (feature, fix, ...).")
@click.option(
    "--shape", type=click.Choice(["merge", "squash", "commit"]), default=None, help="Only this shape."
)
@click.option("--grep", "q", default=None, help="Substring of the title, id or a file path.")
@click.option("--limit", default=30, show_default=True, type=click.IntRange(1, 1000))
@click.option("--offset", default=0, type=click.IntRange(0))
@format_option()
def changes_list(
    repo: str | None,
    category: str | None,
    shape: str | None,
    q: str | None,
    limit: int,
    offset: int,
    fmt: str,
) -> None:
    """List indexed changes, newest merged first."""
    root = _repo_root(repo)
    got = run_async(
        _list(root, category=category, shape=shape, q=q, limit=limit, offset=offset)
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
def changes_show(ref: str, repo: str | None, fmt: str) -> None:
    """Show one change by id (pr-123, change-I..., commit-<sha>) or by a commit sha."""
    root = _repo_root(repo)
    record = run_async(_show(root, ref))
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
