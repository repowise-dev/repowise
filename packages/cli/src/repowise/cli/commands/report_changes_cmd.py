"""``repowise report changes`` - change evidence for a period.

Every change merged between ``--since`` and ``--until`` with whether it carries
a review record, which tests reach it, the local change policy's verdict, the
teams accountable for what it touched, its provenance and its outcomes, plus a
period summary and a method appendix. Built the way ``report assessment``
builds its document and rendered by the same renderers (JSON, Markdown, HTML,
CSV). A change without a kept checklist has one built and stored first, as
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


@dataclass(frozen=True)
class ChangesReportOptions:
    """The command line of ``repowise report changes``, as click parses it."""

    path: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    fmt: str = "markdown"
    out_path: str | None = None
    refresh: bool = False
    no_build: bool = False
    workspace_flag: bool = False
    no_workspace: bool = False
    repo_alias: str | None = None

    def period(self) -> tuple[datetime | None, datetime | None]:
        """``(since, until)`` in UTC; raises when the period is empty."""
        since, until = (d.replace(tzinfo=UTC) if d else None for d in (self.since, self.until))
        if since and until and since >= until:
            raise click.BadParameter("--since must be before --until", param_hint="--since")
        return since, until


@click.command("changes")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option(
    "--since", type=click.DateTime(), default=None, help="Merged on or after this date (UTC)."
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
    from repowise.core.changes.period import PeriodRequest
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
    request = PeriodRequest(
        since=since,
        until=until,
        refresh=opts.refresh,
        build=not opts.no_build,
        plan=build_plan,
        progress=build_progress,
    )
    document = run_async(_build(repos, available_sections("changes"), scope, period=request))
    document["kind"] = "changes"
    _emit(render(document), opts.fmt, opts.out_path, document)
