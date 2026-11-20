"""``repowise report first-look`` - the report written after the first index.

Re-renders the stored report (``.repowise/reports/first-look.json``), or
builds it from the index when there is none (no model, no network, free),
and opens the HTML. ``--format json`` is the agent-readable form, with each
listed item's prompt; ``--since-last`` compares the report with the index now.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import (
    err_console,
    repo_index_session,
    run_async,
    silence_logs_for_machine_output_until_close,
)

FORMATS = ("html", "md", "json")


def _web_url(root: Path) -> str | None:
    from repowise.cli.commands.serve_cmd._lock import live_web_url

    return live_web_url(root)


async def _report(session: Any, repo_id: str, root: Path, web: str | None) -> Any:
    """The stored report: the checkout's file, else the index row, else a new one."""
    from repowise.core.report.first_look.store import load_report, read_report_file
    from repowise.core.report.first_look.write import write_first_look

    report = read_report_file(root) or await load_report(session, repo_id)
    if report is None:
        err_console.print("[dim]No first-look report yet; writing it from the index.[/dim]")
        report = await write_first_look(session, repo_id, root, app_url=web)
    return report


def _since_last_text(report: Any, found: Any) -> str:
    from repowise.core.report.first_look.text import item_text, title
    from repowise.core.report.first_look.write import with_prose

    texts = with_prose(report).prose.get("items") or {}
    was = (found.report_commit or "")[:8] or "an unknown commit"
    now = (found.current_commit or "")[:8]
    head = f"Written at {was}." + (
        f" The index is now at {now}." if found.behind else " The index has not moved since."
    )
    lines = [
        f"{head} {len(found.fixed)} fixed, {len(found.changed)} changed, {len(found.new)} new.",
    ]

    def row(item: Any, text: str) -> str:
        loc = item.locations[0] if item.locations else None
        where = f" ({loc.path}{f':{loc.line}' if loc.line else ''})" if loc else ""
        return f"- {title(item.section).split(':')[0]}: {text}{where}"

    if found.fixed:
        lines += ["", "Fixed:"]
        lines += [row(report.item(i), texts.get(i, "")) for i in found.fixed if report.item(i)]
    # A changed item reads as it is now, not as the report wrote it.
    if found.changed_items:
        lines += ["", "Changed:"]
        lines += [row(i, item_text(i)) for i in found.changed_items]
    if found.new:
        lines += ["", "New since the report:"]
        lines += [row(i, item_text(i)) for i in found.new]
    if found.unknown:
        lines += ["", "Not compared: " + ", ".join(f"{title(k)} ({v})" for k, v in found.unknown.items())]
    return "\n".join(lines) + "\n"


async def _run(root: Path, fmt: str, since_last: bool) -> str:
    """The text to emit."""
    from repowise.core.report.first_look.staleness import staleness
    from repowise.core.report.first_look.write import RENDERED, render_document
    from repowise.core.report.render import render_json, report_renderers

    async with repo_index_session(root) as opened:
        if opened is None:
            raise click.ClickException(f"No index for {root}. Run `repowise init` there first.")
        session, repo_id = opened
        web = _web_url(root)
        report = await _report(session, repo_id, root, web)
        if since_last:
            found = await staleness(session, repo_id, report, root)
            if fmt == "json":
                return render_json(found.to_json())
            return _since_last_text(report, found)
        doc = await render_document(session, repo_id, report, app_url=web)
    if fmt == "json":
        return render_json(doc)
    return report_renderers.get(RENDERED[f".{fmt}"])(doc)


@click.command("first-look")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option(
    "--format",
    "fmt",
    type=click.Choice(FORMATS),
    default="html",
    show_default=True,
    help="html (written and opened), md, or json (for agents: the report with each item's prompt).",
)
@click.option("--out", "out_path", type=click.Path(dir_okay=False), help="Write to this file.")
@click.option(
    "--since-last",
    is_flag=True,
    help="List what was fixed, changed or found new since the report was written.",
)
def first_look_command(path: str | None, fmt: str, out_path: str | None, since_last: bool) -> None:
    """The first-look report: what Repowise found at the first index, most urgent
    first, with a path, a link and an agent prompt per item. Free: no model."""
    from repowise.core.report.first_look.write import rendered_path

    root = Path(path or ".").resolve()
    to_stdout = not out_path and (fmt != "html" or since_last)
    if to_stdout:
        silence_logs_for_machine_output_until_close()  # stdout carries only the report
    text = run_async(_run(root, fmt, since_last))
    if to_stdout:
        click.echo(text, nl=False)
        return
    target = Path(out_path) if out_path else rendered_path(root, ".html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    err_console.print(f"Wrote the first-look report to {target}")
    if fmt == "html" and not out_path and sys.stdout.isatty():
        click.launch(str(target))
