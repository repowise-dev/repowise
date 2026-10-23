"""``repowise report estate`` - the estate report for a workspace.

The ``estate`` report sections, which read the workspace's stored cross-repo
artifacts (system graph, contracts, diagnostics) once, with every member
repository's indexed commit listed. Built and rendered the way
``report assessment`` builds its document.
"""

from __future__ import annotations

import click

from repowise.cli.helpers import (
    err_console,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output_until_close,
)


@click.command("estate")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option("--sections", "wanted", default=None, help="Comma-separated section ids.")
@click.option(
    "--format",
    "fmt",
    default="markdown",
    show_default=True,
    help="Renderer: json, markdown, html or csv (or one a plugin registers).",
)
@click.option("--out", "out_path", type=click.Path(dir_okay=False), help="Write to this file.")
def estate_command(path: str | None, wanted: str | None, fmt: str, out_path: str | None) -> None:
    """Estate report: repositories and services, the system map and architecture with
    the evidence behind them, contracts and the calls that reached no provider, health
    and churn per repo, the riskiest seams and where to start."""
    from repowise.cli._helpers.target import WorkspaceNotFound
    from repowise.cli.commands.report_assessment_cmd import (
        _build,
        _emit,
        _pick_sections,
        _renderer,
        _targets,
    )

    if not out_path:
        silence_logs_for_machine_output_until_close()  # stdout carries only the report
    sections = _pick_sections(wanted, "estate")
    render = _renderer(fmt)
    try:
        target = resolve_command_target(path=path, workspace_flag=True)
    except WorkspaceNotFound as exc:
        raise click.ClickException(
            "report estate covers a workspace, and no .repowise-workspace.yaml was found "
            "at or above this directory. Run 'repowise init <workspace-dir>' first."
        ) from exc
    target.notice(err_console, command="report estate")
    scope = {"kind": "workspace", "name": target.ws_root.name}
    document = run_async(_build(_targets(target), sections, scope, ws_root=target.ws_root))
    document["kind"] = "estate"
    _emit(render(document), fmt, out_path, document)
