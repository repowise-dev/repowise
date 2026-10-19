"""``repowise suspects`` - changes merged before a symptom, in the order to read them.

Ranks the indexed Change Records in a window (14 days before ``--until`` by
default) against what was seen: free text, files, a stack trace. Each row
lists the signals that fired and their evidence. The order is where to start
reading, never a cause; ``git bisect run`` confirms one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click

from repowise.cli.helpers import console, repo_index_session, resolve_command_target, run_async
from repowise.cli.output import emit_json, format_option


async def _suspects(root: Path, symptom, since, until, limit: int) -> dict | None:
    from repowise.core.suspects.index import find_suspects

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        return await find_suspects(
            session, repo_id, root, symptom, since=since, until=until, limit=limit
        )


@click.command("suspects")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@click.option("--symptom", default="", help="What was seen: an issue's text, an error message.")
@click.option("--files", "files", multiple=True, help="A file the symptom involves (repeatable).")
@click.option(
    "--trace",
    "trace",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="A file holding a stack trace.",
)
@click.option(
    "--since", type=click.DateTime(), default=None, help="Merged on or after (UTC); default 14 days before --until."
)
@click.option(
    "--until", type=click.DateTime(), default=None, help="Merged before (UTC): when the symptom was seen; default now."
)
@click.option("--limit", default=10, show_default=True, type=click.IntRange(1, 200))
@format_option()
def suspects_command(
    repo: str | None,
    symptom: str,
    files: tuple[str, ...],
    trace: Path | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
    fmt: str,
) -> None:
    """Rank the changes merged in a window against a symptom, with the evidence for each."""
    from repowise.core.suspects import Symptom
    from repowise.core.suspects.rank import MAX_SYMPTOM_CHARS, MAX_SYMPTOM_FILES

    target = resolve_command_target(path=repo, no_workspace_flag=True)
    assert target.repo_path is not None
    root = Path(target.repo_path).resolve()
    since, until = (d.replace(tzinfo=UTC) if d else None for d in (since, until))
    trace_text = trace.read_text(encoding="utf-8", errors="replace") if trace else ""
    if not (symptom.strip() or files or trace_text.strip()):
        raise click.UsageError("Give a symptom: --symptom, --files or --trace.")
    if len(symptom) > MAX_SYMPTOM_CHARS or len(files) > MAX_SYMPTOM_FILES:
        raise click.UsageError(
            f"Keep --symptom under {MAX_SYMPTOM_CHARS} characters and --files to {MAX_SYMPTOM_FILES};"
            " only the head of a long log is read anyway."
        )
    got = run_async(
        _suspects(root, Symptom(text=symptom, files=files, trace=trace_text, at=until), since, until, limit)
    )
    if got is None:
        console.print("No index found. Run `repowise init` first.")
        raise SystemExit(1)
    if fmt == "json":
        emit_json(got)
        return
    from rich.markup import escape
    from rich.table import Table

    window = got["window"]
    console.print(
        f"{got['candidates']} change(s) merged {window['since'][:16]} to {window['until'][:16]}"
    )
    if not got["suspects"]:
        return
    table = Table("#", "Change", "Merged", "Title", "Signals (rank: evidence)")
    for row in got["suspects"]:
        signals = "\n".join(
            f"{name} {sig['rank']}: {escape('; '.join(sig['evidence'][:2]))}"
            for name, sig in row["signals"].items()
        )
        table.add_row(
            str(row["rank"]),
            escape(row.get("ref") or row["id"][:19]),
            (row["merged_at"] or "")[:10],
            escape(row["title"][:70]),
            signals,
        )
    console.print(table)
    if got.get("texts_missing"):
        console.print(f"[dim]{got['texts_missing']} change(s) git could not read: ranked on title and files.[/dim]")
    console.print(f"[dim]{got['note']}[/dim]")
