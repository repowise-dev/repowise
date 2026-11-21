"""``repowise mark ID STATUS`` - record your answer to a repo report item.

A thin caller of :func:`repowise.core.report.first_look.marks.mark_item`: the
answer goes to every row the item stands for, in the stores the web app and
``repowise next`` read, so ``repowise report repo`` shows it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import (
    console,
    err_console,
    repo_index_session,
    run_async,
    silence_logs_for_machine_output_until_close,
)
from repowise.cli.output import emit_json

#: What to do next, by error code, for a person at the terminal.
HINTS = {
    "reason_required": "Say why with --reason.",
    "unknown_item": "Ids are listed by `repowise report --format json`.",
}

#: The statuses as typed here; ``false-positive`` reaches core as ``false_positive``.
STATUSES = ("done", "ack", "false-positive", "snooze")


async def _mark(root: Path, item_id: str, status: str, reason: str | None, days: int | None) -> Any:
    from repowise.core.report.first_look.marks import MarkError, mark_item

    async with repo_index_session(root) as opened:
        if opened is None:
            raise MarkError("no_index", f"No index for {root}. Run `repowise init` there first.")
        session, repo_id = opened
        out = await mark_item(
            session, repo_id, item_id, status, reason=reason, days=days, repo_root=root
        )
        await session.commit()
        return out


def _said(out: dict[str, Any]) -> str:
    status = out["status"].replace("_", " ")
    until = f" until {out['until'][:10]}" if out.get("until") else ""
    return f"Marked {out['id']} {status}{until}."


@click.command("mark")
@click.argument("item_id", metavar="ID")
@click.argument("status", type=click.Choice(STATUSES))
@click.option("--reason", default=None, help="Why: required for ack and false-positive.")
@click.option(
    "--days",
    type=click.IntRange(1, 365),
    default=None,
    help="With snooze: how many days (default 14).",
)
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print {id, store, status, reason, until} as JSON; an error goes to stderr as JSON.",
)
def mark_command(
    item_id: str, status: str, reason: str | None, days: int | None, repo: str | None, as_json: bool
) -> None:
    """Mark a repo report item done, acknowledged (ack), a false positive or
    snoozed. ID is the item's id (fl_...) from `repowise report --format json`."""
    from repowise.core.report.first_look.marks import MarkError

    if days is not None and status != "snooze":
        raise click.UsageError("--days applies to snooze only.")
    if as_json:
        silence_logs_for_machine_output_until_close()  # stdout carries only the result
    root = Path(repo or ".").resolve()
    try:
        out = run_async(_mark(root, item_id, status, reason, days))
    except MarkError as exc:
        if as_json:
            # stdout carries only a result; an error is JSON on stderr.
            click.echo(json.dumps(exc.to_json(), indent=2), err=True)
        else:
            hint = HINTS.get(exc.code)
            said = f"{exc.message} {hint}" if hint else exc.message
            err_console.print(said, style="red", markup=False)
        raise SystemExit(1) from None
    if as_json:
        emit_json(out)
    else:
        console.print(_said(out))
