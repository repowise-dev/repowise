"""``repowise report repo`` (alias ``first-look``) - the repo report.

Renders the living report: the stored report (``.repowise/reports/first-look.json``,
written from the index when there is none: no model, no network, free) as the
index has it now, with each item's status (``repowise mark``), what is new and
what was resolved since. Opens the HTML. ``--format json`` is the
agent-readable form; ``--since-last`` lists what changed since the report.
``--llm`` writes a new report whose prose one model call writes, after the estimate.
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


def first_look_estimate(provider: Any, model: str | None = None) -> str:
    """``about $0.003 with <model>``: what the report's one model call costs."""
    from repowise.core.cost_estimator import estimate_first_look_cost
    from repowise.core.report.first_look.llm import first_look_model

    chosen = first_look_model(provider, model)
    est = estimate_first_look_cost(provider.provider_name, chosen)
    return f"about ${est.estimated_cost_usd:.3f} with {chosen}"


def first_look_plan_line(repo_path: Path, provider: Any) -> str | None:
    """Init's cost-plan line for the report's model call; ``None`` when init will
    not make it (the setting is off, or a stamped report is kept). Init writes
    a report when :func:`read_report_file` finds none it can read, so this asks
    the same helper."""
    from repowise.core.report.first_look.config import first_look_settings
    from repowise.core.report.first_look.store import read_report_file

    try:
        settings = first_look_settings(repo_path)
    except Exception:  # a broken config line must not stop the plan
        return None
    if not (settings.enabled and settings.llm) or read_report_file(repo_path) is not None:
        return None
    return f"Repo report {first_look_estimate(provider, settings.model)} (one model call)."


def _rewrite_with_model(root: Path, yes: bool) -> bool:
    """Write a new report whose prose one model call writes, after the estimate;
    ``False`` when the person declines."""
    import dataclasses

    from repowise.cli.helpers import resolve_provider
    from repowise.cli.providers import build_cost_tracker, flush_cost_tracker
    from repowise.core.report.first_look.config import first_look_settings
    from repowise.core.report.first_look.write import write_first_look

    provider = resolve_provider(None, None, repo_path=root)
    # Asking for it is the opt-in: the settings only stop the automatic call.
    settings = dataclasses.replace(first_look_settings(root), enabled=True, llm=True)
    err_console.print(f"Repo report {first_look_estimate(provider, settings.model)}.")
    if not yes and not click.confirm("Write it with the model?", default=True, err=True):
        err_console.print("Not written; the stored report is unchanged.")
        return False
    tracker = build_cost_tracker(root, root.name)
    provider._cost_tracker = tracker

    async def write() -> Any:
        async with repo_index_session(root) as opened:
            if opened is None:
                raise click.ClickException(f"No index for {root}. Run `repowise init` there first.")
            session, repo_id = opened
            return await write_first_look(
                session, repo_id, root, provider=provider, settings=settings, app_url=_web_url(root)
            )

    try:
        report = run_async(write())
    finally:
        flush_cost_tracker(tracker)
    stamp = report.stamp
    cost = f", ${stamp.cost:.3f}" if stamp.cost is not None else ""
    if stamp.generator == "template":
        err_console.print(f"The model's reply was unusable; kept the template text{cost}.")
    else:
        err_console.print(f"Written by {stamp.generator}{cost}.")
    return True


_WRITING = "[dim]No repo report yet; writing it from the index.[/dim]"


async def _report(session: Any, repo_id: str, root: Path, web: str | None) -> Any:
    """The stored report: the checkout's file, else the index row, else a new one."""
    from repowise.core.report.first_look.living import load_baseline
    from repowise.core.report.first_look.write import write_first_look

    report = await load_baseline(session, repo_id, root)
    if report is None:
        err_console.print(_WRITING)
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
        # A grouped item names its first few places.
        places = [f"{loc.path}{f':{loc.line}' if loc.line else ''}" for loc in item.locations[:3]]
        more = max(item.locations_total, len(item.locations)) - len(places)
        where = f" ({', '.join(places)}{f' and {more} more' if more > 0 else ''})" if places else ""
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


def agent_json(doc: dict[str, Any]) -> dict[str, Any]:
    """The report document as ``--format json`` prints it: every item with its
    id, section, rule, ``status``, ``change`` (``new``, ``changed``,
    ``resolved`` or null), locations, effort and confidence, and no prompt
    (``--prompt`` prints the whole-report one)."""
    out = {k: v for k, v in doc.items() if k not in ("prompts", "report_prompt")}
    items = [i for s in out.get("sections") or () for i in s.get("items") or ()]
    for item in (*items, *((out.get("baseline") or {}).get("resolved") or ())):
        item.pop("prompt", None)
    return out


async def _run(root: Path, fmt: str, since_last: bool, prompt: bool = False) -> str:
    """The text to emit."""
    from repowise.core.report.first_look.living import build_living, load_baseline
    from repowise.core.report.first_look.prompts import render_report_prompt
    from repowise.core.report.first_look.staleness import staleness
    from repowise.core.report.first_look.write import RENDERED, render_document
    from repowise.core.report.render import render_json, report_renderers

    async with repo_index_session(root) as opened:
        if opened is None:
            raise click.ClickException(f"No index for {root}. Run `repowise init` there first.")
        session, repo_id = opened
        web = _web_url(root)
        if since_last:
            report = await _report(session, repo_id, root, web)
            found = await staleness(session, repo_id, report, root)
            if fmt == "json":
                return render_json(found.to_json())
            return _since_last_text(report, found)
        if await load_baseline(session, repo_id, root) is None:
            err_console.print(_WRITING)
        living = await build_living(session, repo_id, root, store_missing=True, app_url=web)
        if prompt:
            # The prompt the web page's "Hand to agent" copies: open items only.
            statuses = {i: s.status for i, s in living.statuses.items()}
            return render_report_prompt(living.report, statuses=statuses) + "\n"
        # The HTML page holds each item's prompt; the Markdown and JSON exports none.
        doc = await render_document(session, repo_id, living, app_url=web, prompts=fmt == "html")
    if fmt == "json":
        return render_json(agent_json(doc))
    return report_renderers.get(RENDERED[f".{fmt}"])(doc)


@click.command("repo")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option(
    "--format",
    "fmt",
    type=click.Choice(FORMATS),
    default="html",
    show_default=True,
    help="html (written and opened), md, or json (for agents: each item with its id and status).",
)
@click.option("--out", "out_path", type=click.Path(dir_okay=False), help="Write to this file.")
@click.option(
    "--since-last",
    is_flag=True,
    help="List what was fixed, changed or found new since the report was written.",
)
@click.option(
    "--prompt",
    is_flag=True,
    help="Print the whole-report agent prompt: the open items with their ids and how to mark each.",
)
@click.option(
    "--llm",
    is_flag=True,
    help="Rewrite the report at HEAD with one call to the configured model. Prints the cost first.",
)
@click.option("--yes", "-y", is_flag=True, help="With --llm, skip the cost confirmation.")
def first_look_command(
    path: str | None,
    fmt: str,
    out_path: str | None,
    since_last: bool,
    prompt: bool,
    llm: bool,
    yes: bool,
) -> None:
    """The repo report: what Repowise found, most urgent first, as the index has
    it now, with each item's status. Free unless --llm.

    \b
    For an agent: read the items, fix one, then record it by its id
      repowise report --format json && repowise mark fl_3a91c0d2e4f1 done --json
    """
    from repowise.core.report.first_look.write import rendered_path

    if prompt and since_last:
        raise click.UsageError("--prompt and --since-last are separate views; pick one.")
    root = Path(path or ".").resolve()
    if llm and not _rewrite_with_model(root, yes):
        return
    to_stdout = not out_path and (fmt != "html" or since_last or prompt)
    if to_stdout:
        silence_logs_for_machine_output_until_close()  # stdout carries only the report
    text = run_async(_run(root, fmt, since_last, prompt))
    if to_stdout:
        click.echo(text, nl=False)
        return
    target = Path(out_path) if out_path else rendered_path(root, ".html")
    _write(text, target, open_it=fmt == "html" and not out_path)


def _write(text: str, target: Path, *, open_it: bool) -> None:
    """Write the rendered report to *target*; open the default HTML at a terminal."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    err_console.print(f"Wrote the repo report to {target}")
    if open_it and sys.stdout.isatty():
        click.launch(str(target))


#: ``repowise report first-look``: the name the report first shipped under.
first_look_alias = click.Command(
    "first-look",
    callback=first_look_command.callback,
    params=first_look_command.params,
    help="Alias for `repowise report repo`.",
)
