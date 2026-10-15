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

from datetime import UTC, datetime

import click

from repowise.cli.helpers import (
    err_console,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output_until_close,
)


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
def changes_command(
    path: str | None,
    since: datetime | None,
    until: datetime | None,
    fmt: str,
    out_path: str | None,
    refresh: bool,
    no_build: bool,
    workspace_flag: bool,
    no_workspace: bool,
    repo_alias: str | None,
) -> None:
    """Change evidence for a period: review record, reaching tests, policy result,
    owning teams, provenance and outcomes of every change merged in it.

    A change with no kept evidence checklist has one built and stored in the
    index first (a few seconds each), unless --no-build is given.
    """
    from repowise.cli.commands.changes_cmd import build_plan, build_progress
    from repowise.cli.commands.report_assessment_cmd import _build, _emit, _scope
    from repowise.core.report.build import available_sections
    from repowise.core.report.render import report_renderers

    # Building a checklist parses code, which logs; stdout carries only the report.
    silence_logs_for_machine_output_until_close()
    if fmt not in report_renderers.names:
        raise click.BadParameter(
            f"unknown format {fmt!r}; available: {', '.join(report_renderers.names)}",
            param_hint="--format",
        )
    since, until = (d.replace(tzinfo=UTC) if d else None for d in (since, until))
    if since and until and since >= until:
        raise click.BadParameter("--since must be before --until", param_hint="--since")
    target = resolve_command_target(
        path=path,
        workspace_flag=workspace_flag,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    target.notice(err_console, command="report changes")
    repos, scope = _scope(target)
    scope["period"] = {
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
    }
    document = run_async(
        _build(
            repos,
            available_sections("changes"),
            scope,
            since=since,
            until=until,
            refresh=refresh,
            build=not no_build,
            plan=build_plan,
            progress=build_progress,
        )
    )
    document["kind"] = "changes"
    _emit(report_renderers.get(fmt)(document), fmt, out_path, document)
