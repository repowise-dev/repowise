"""``repowise events`` - typed events from consecutive index runs.

Each ``repowise init`` or ``update`` snapshots the components; when two runs
in a row share an analyzer stamp, what moved between them is stored as
events. This command reads them back, oldest first, as a table, JSON, or
CloudEvents JSON lines (the shape the ``jsonl`` sink writes).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click

from repowise.cli.ci import ci_notices
from repowise.cli.helpers import console, repo_index_session, resolve_command_target, run_async
from repowise.cli.output import emit_json, format_option

_RELATIVE = re.compile(r"^(\d+)([dh])$")


def _parse_since(value: str | None) -> datetime | None:
    """``7d``, ``12h``, or an ISO date or datetime (UTC when no zone is given)."""
    if not value:
        return None
    m = _RELATIVE.match(value.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return datetime.now(UTC) - (timedelta(days=n) if unit == "d" else timedelta(hours=n))
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise click.BadParameter(f"expected 7d, 12h or an ISO date, got {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _read(root: Path, since: datetime | None, kinds: tuple[str, ...], limit: int):
    """``(events, snapshot runs stored)``, or ``None`` without an index."""
    from repowise.core.events.store import list_events, snapshot_run_count

    async with repo_index_session(root) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        events = await list_events(session, repo_id, since=since, kinds=kinds, limit=limit)
        return events, await snapshot_run_count(session, repo_id)


@click.command("events")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@click.option("--since", default=None, help="Only events from then on: 7d, 12h or an ISO date.")
@click.option("--kind", "kinds", multiple=True, help="Only this kind (repeatable).")
@click.option("--limit", default=200, show_default=True, type=click.IntRange(1, 10000))
@click.option("--repo", "repo_alias", default=None, help="In workspace mode, target one repo.")
@click.option("--no-workspace", is_flag=True, default=False, help="Force single-repo mode.")
@format_option(choices=("table", "json", "jsonl"))
def events_command(
    repo: str | None,
    since: str | None,
    kinds: tuple[str, ...],
    limit: int,
    repo_alias: str | None,
    no_workspace: bool,
    fmt: str,
) -> None:
    """List typed events (new hotspot, bus factor drop, ...) oldest first.

    Events belong to one repo: from a workspace root this reads the primary
    repo; pass --repo <alias> for another.
    """
    from repowise.core.events.model import KINDS, to_cloudevent

    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise click.BadParameter(f"unknown kind(s) {unknown}; known: {sorted(KINDS)}")
    target = resolve_command_target(
        path=repo, no_workspace_flag=no_workspace, repo_alias=repo_alias
    )
    root = target.narrow(ci_notices(fmt), command="events").resolve()
    read = run_async(_read(root, _parse_since(since), kinds, limit))
    if read is None:
        console.print("No index found. Run `repowise init` first.")
        raise SystemExit(1)
    events, runs = read
    if fmt == "json":
        emit_json({"events": [to_cloudevent(e) for e in events]})
        return
    if fmt == "jsonl":
        for e in events:
            click.echo(json.dumps(to_cloudevent(e), sort_keys=True))
        return
    from rich.markup import escape
    from rich.table import Table

    if not events:
        from repowise.core.events.store import empty_events_reason

        # An empty list is only news once two runs could be compared.
        console.print(empty_events_reason(runs, filtered=bool(since or kinds))[1])
        return
    table = Table("When", "Kind", "Component", "Severity", "Evidence")
    for e in events:
        evidence = ", ".join(f"{k}={v}" for k, v in e.evidence.items() if k != "basis")
        table.add_row(
            e.occurred_at.strftime("%Y-%m-%d %H:%M"),
            e.kind,
            escape(e.component or ""),
            e.severity,
            escape(evidence[:100]),
        )
    console.print(table)
    console.print(f"[dim]{len(events)} event(s).[/dim]")
