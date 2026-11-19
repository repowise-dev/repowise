"""``repowise report inventory`` - the software inventory for a repo or workspace.

The ``inventory`` report section alone, built and rendered the way
``report assessment`` builds its document (JSON, Markdown or HTML).
"""

from __future__ import annotations

import click

from repowise.cli.helpers import (
    err_console,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output_until_close,
)


@click.command("inventory")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option(
    "--format",
    "fmt",
    default="markdown",
    show_default=True,
    help="Renderer: json, markdown, html or csv (or one a plugin registers).",
)
@click.option("--out", "out_path", type=click.Path(dir_okay=False), help="Write to this file.")
@click.option("--workspace", "workspace_flag", is_flag=True, help="Cover every workspace repo.")
@click.option("--no-workspace", is_flag=True, help="Force single-repo mode.")
@click.option("--repo", "repo_alias", default=None, help="Workspace repo alias to cover.")
def inventory_command(
    path: str | None,
    fmt: str,
    out_path: str | None,
    workspace_flag: bool,
    no_workspace: bool,
    repo_alias: str | None,
) -> None:
    """Software inventory: components, owner teams, languages, entry points, packages."""
    from repowise.cli.commands.report_assessment_cmd import (
        _build,
        _emit,
        _pick_sections,
        _renderer,
        _scope,
    )

    if not out_path:
        silence_logs_for_machine_output_until_close()  # stdout carries only the report
    render = _renderer(fmt)
    sections = _pick_sections("inventory")
    target = resolve_command_target(
        path=path,
        workspace_flag=workspace_flag,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    target.notice(err_console, command="report inventory")
    repos, scope = _scope(target)
    document = run_async(_build(repos, sections, scope))
    document["kind"] = "inventory"
    _emit(render(document), fmt, out_path, document)
