"""``repowise report changes`` - change evidence for a period.

Every change merged between ``--since`` and ``--until`` with whether it carries
a review record, which tests reach it, the local change policy's verdict, the
teams accountable for what it touched, its provenance and its outcomes, plus a
period summary and a method appendix. Without ``--since`` the period is the 90
days before the newest indexed commit; ``--since all`` covers every change.
Built the way ``report assessment`` builds its document and rendered by the
same renderers (JSON, Markdown, HTML, CSV). A change without a kept checklist has one built and stored first, as
``repowise changes export`` does, unless ``--no-build``; the count to build,
an estimate and progress go to stderr.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import click

from repowise.cli.helpers import (
    err_console,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output_until_close,
)

#: ``--since all``: every indexed change rather than the default window.
ALL_HISTORY = "all"


def _since(ctx: click.Context, param: click.Parameter, value: str | None) -> datetime | str | None:
    """``--since``: a date, :data:`ALL_HISTORY`, or unset for the default window."""
    if value is None:
        return None
    if value.strip().lower() == ALL_HISTORY:
        return ALL_HISTORY
    return click.DateTime().convert(value, param, ctx)


@dataclass(frozen=True)
class ChangesReportOptions:
    """The command line of ``repowise report changes``, as click parses it."""

    path: str | None = None
    since: datetime | str | None = None
    until: datetime | None = None
    fmt: str = "markdown"
    out_path: str | None = None
    refresh: bool = False
    no_build: bool = False
    workspace_flag: bool = False
    no_workspace: bool = False
    repo_alias: str | None = None

    @property
    def whole_history(self) -> bool:
        return self.since == ALL_HISTORY

    def period(self) -> tuple[datetime | None, datetime | None]:
        """``(since, until)`` in UTC; raises when the period is empty."""
        start = None if self.whole_history else self.since
        since, until = (d.replace(tzinfo=UTC) if d else None for d in (start, self.until))
        if since and until and since >= until:
            raise click.BadParameter("--since must be before --until", param_hint="--since")
        return since, until


@click.command("changes")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option(
    "--since",
    default=None,
    callback=_since,
    metavar="DATE|all",
    # The day count is DEFAULT_WINDOW_DAYS, written out to keep `--help` off the core import.
    help="Merged on or after this date (UTC); default the 90 days before the newest indexed "
    "commit, `all` for every indexed change.",
)
@click.option("--until", type=click.DateTime(), default=None, help="Merged before this date (UTC).")
@click.option(
    "--format",
    "fmt",
    default="markdown",
    show_default=True,
    help="Renderer: json, markdown, html or csv (or one a plugin registers).",
)
@click.option("--out", "out_path", type=click.Path(dir_okay=False), help="Write to this file.")
@click.option("--refresh", is_flag=True, help="Rebuild every evidence checklist in the period.")
@click.option(
    "--no-build",
    is_flag=True,
    help="Build nothing: report kept checklists only and count the rest as unknown.",
)
@click.option("--workspace", "workspace_flag", is_flag=True, help="Cover every workspace repo.")
@click.option("--no-workspace", is_flag=True, help="Force single-repo mode.")
@click.option("--repo", "repo_alias", default=None, help="Workspace repo alias to cover.")
def changes_command(**params: Any) -> None:
    """Change evidence for a period: review record, reaching tests, policy result,
    owning teams, provenance and outcomes of every change merged in it.

    A change with no kept evidence checklist has one built and stored in the
    index first (a few seconds each), unless --no-build is given.
    """
    from repowise.cli.commands.changes_cmd import build_plan, build_progress
    from repowise.cli.commands.report_assessment_cmd import _build, _emit, _renderer, _scope
    from repowise.core.changes.period import DEFAULT_WINDOW_DAYS, PeriodRequest
    from repowise.core.report.build import available_sections

    opts = ChangesReportOptions(**params)
    # Building a checklist parses code, which logs; stdout carries only the report.
    silence_logs_for_machine_output_until_close()
    render = _renderer(opts.fmt)
    since, until = opts.period()
    target = resolve_command_target(
        path=opts.path,
        workspace_flag=opts.workspace_flag,
        no_workspace_flag=opts.no_workspace,
        repo_alias=opts.repo_alias,
    )
    target.notice(err_console, command="report changes")
    repos, scope = _scope(target)
    scope["period"] = {
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
    }
    window_days = None if opts.whole_history else DEFAULT_WINDOW_DAYS
    if since is None and window_days:
        # Each repo's start is measured from its own newest commit; the summary names it.
        scope["period"]["window_days"] = window_days
    request = PeriodRequest(
        since=since,
        until=until,
        window_days=window_days,
        refresh=opts.refresh,
        build=not opts.no_build,
        plan=build_plan,
        progress=build_progress,
    )
    document = run_async(_build(repos, available_sections("changes"), scope, period=request))
    document["kind"] = "changes"
    _emit(render(document), opts.fmt, opts.out_path, document)
