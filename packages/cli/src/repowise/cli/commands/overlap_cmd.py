"""``repowise overlap`` - other open branches editing the files this change edits.

Git answers who touches the same file; the repository's own index, when there
is one, orders those files and adds the ones history pairs with them. Without
an index the git answer stands, so the command works in a fresh clone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import (
    console,
    repo_index_session,
    resolve_command_target,
    run_async,
)
from repowise.cli.output import emit_json, format_option
from repowise.core.analysis.branch_overlap import BRANCH_SCAN_LIMIT, BranchOverlap


def _require_ref(root: Path, ref: str) -> None:
    """Refuse a ref that names no commit: git diffs it to nothing, which reads
    as "no changes" rather than as the pruned remote or typo it is."""
    from repowise.core import git_refs

    if not git_refs.resolve(str(root), ref):
        raise click.ClickException(f"Unknown ref {ref!r}.")


def _repo_path(path: str | None) -> Path:
    """The repository to read. One repo, always: a branch scan is one repo's git."""
    target = resolve_command_target(path=path, no_workspace_flag=True)
    assert target.repo_path is not None
    return Path(target.repo_path).resolve()


async def _overlap(root: Path, changed: list[str], **kwargs: Any) -> BranchOverlap:
    """The overlap for *changed*, using the repository's index when it is readable."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.analysis.branch_overlap import rank_with_index, scan_branches

    scan = scan_branches(str(root), changed, **kwargs)
    # The index is opened only to rank what git already found.
    async with repo_index_session(root) as opened:
        if opened is not None:
            session, repo_id = opened
            try:
                return await rank_with_index(session, repo_id, scan)
            except (SQLAlchemyError, OSError, LookupError):
                # An index written by an older version can fail the query itself.
                pass
    # An absent, stale or locked index costs the ranking, not the answer.
    return scan.overlap


def _render(data: dict[str, Any]) -> None:
    """The branches and the files they share, with each row's basis in words."""
    from rich.markup import escape

    summary = escape(str(data.get("summary") or ""))
    branches = data.get("branches") or []
    if not branches:
        console.print(summary)
        return

    console.print(f"[bold]{summary}[/bold]")
    # One width for every row so the basis column lines up across branches.
    width = max(len(str(row["basis"])) for entry in branches for row in entry["files"])
    for entry in branches:
        console.print(
            f"\n{escape(str(entry['branch']))}  ahead {entry['ahead']}, "
            f"behind {entry['behind']}, last commit {entry['last_commit']}"
        )
        for row in entry["files"]:
            partner = row.get("partner")
            suffix = f"  (with {escape(str(partner))})" if partner else ""
            basis = escape(str(row["basis"])).ljust(width)
            console.print(f"    {basis}  {escape(str(row['file']))}{suffix}")

    if data.get("truncated"):
        console.print(
            f"\n[dim]Scanned the newest {data['scanned']} of {data['total']} branches; "
            "raise --limit to scan more.[/dim]"
        )


@click.command("overlap")
@click.option("--base", default=None, help="Base ref (defaults to the repository's trunk).")
@click.option("--branch", default="HEAD", help="The change to compare. Defaults to HEAD.")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=BRANCH_SCAN_LIMIT,
    show_default=True,
    help="How many branches to diff, newest committer date first.",
)
@format_option()
def overlap_command(
    base: str | None, branch: str, repo: str | None, limit: int, fmt: str
) -> None:
    """Show which other branches edit the files this change edits."""
    from repowise.core import git_refs

    root = _repo_path(repo)
    base = base or git_refs.default_base(str(root))
    _require_ref(root, base)
    _require_ref(root, branch)

    changed = git_refs.changed_files(str(root), base, branch)
    if not changed:
        message = f"Nothing changed on {branch} since {base}."
        if fmt == "json":
            # The same keys as any other run, so a consumer parses one shape.
            empty = BranchOverlap(base, git_refs.current_branch(str(root)) or branch, (), 0, 0)
            emit_json({**empty.to_dict(), "summary": message})
        else:
            console.print(message)
        return

    result = run_async(_overlap(root, changed, base=base, current=branch, limit=limit))
    if fmt == "json":
        emit_json(result.to_dict())
        return
    _render(result.to_dict())
