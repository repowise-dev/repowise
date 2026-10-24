"""``repowise report assessment`` - the assessment pack for a repo or workspace.

Reads the index only (no LLM, no network, no writes) and prints or writes one
document: JSON (canonical), Markdown or self-contained HTML. In a workspace it
covers every repo, or the one ``--repo`` names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import (
    cross_repo_cut_inputs,
    err_console,
    repo_index_session,
    resolve_command_target,
    run_async,
    silence_logs_for_machine_output_until_close,
)


def _targets(target: Any) -> list[tuple[str | None, Path]]:
    """``(alias, path)`` per repository the report covers."""
    if not target.is_workspace:
        return [(None, Path(target.repo_path).resolve())]
    if target.repo_filter is not None:
        return [(target.repo_filter, target.single_repo_path())]
    return [(r.alias, (target.ws_root / r.path).resolve()) for r in target.ws_config.repos]


async def _build(
    repos: list[tuple[str | None, Path]],
    sections: list,
    scope: dict,
    *,
    ws_root: Path | None = None,
    **options: Any,
) -> dict:
    """The document over *repos*; *options* go to each :class:`ReportContext`.

    Members of the workspace at *ws_root* read dead code with its cross-repo cut.
    Over a whole workspace, its workspace-scope sections run once after the repos.
    """
    from repowise.core.persistence.models import Repository
    from repowise.core.report.build import assemble, collect_repo, collect_workspace
    from repowise.core.report.model import ReportContext, WorkspaceReportContext

    parts = []
    for alias, root in repos:
        async with repo_index_session(root) as opened:
            if opened is None:
                if scope["kind"] == "repo":
                    raise click.ClickException(
                        f"No index for {root}. Run `repowise init` there first."
                    )
                entry = {"alias": alias, "name": root.name, "indexed": False}
                parts.append((entry, []))
                continue
            session, repo_id = opened
            repo = await session.get(Repository, repo_id)
            ctx = ReportContext(
                session,
                repo_id,
                root,
                alias=alias,
                head_commit=repo.head_commit if repo else None,
                cross_repo=cross_repo_cut_inputs(root, ws_root),
                **options,
            )
            parts.append(await collect_repo(ctx, sections))
    workspace = []
    if ws_root is not None and scope["kind"] == "workspace":
        ws_ctx = WorkspaceReportContext(ws_root, repos=[entry for entry, _ in parts])
        workspace = await collect_workspace(ws_ctx, sections)
    return assemble(scope, parts, workspace=workspace)


def _pick_sections(wanted: str | None, report: str = "assessment") -> list:
    """The registered *report* sections, narrowed to *wanted* (comma-separated ids)."""
    from repowise.core.report.build import available_sections

    sections = available_sections(report)
    if not wanted:
        return sections
    names = [n.strip() for n in wanted.split(",") if n.strip()]
    known = dict(sections)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise click.BadParameter(
            f"unknown section(s) {', '.join(unknown)}; available: {', '.join(known)}",
            param_hint="--sections",
        )
    return [(n, s) for n, s in sections if n in names]


def _scope(target: Any) -> tuple[list[tuple[str | None, Path]], dict]:
    """``(repos, scope)`` for the resolved command target."""
    repos = _targets(target)
    if target.is_workspace and target.repo_filter is None:
        return repos, {"kind": "workspace", "name": target.ws_root.name}
    return repos, {"kind": "repo", "name": repos[0][1].name}


def _renderer(fmt: str) -> Any:
    """The renderer *fmt* names on the ``report-renderer`` seam; a usage error otherwise."""
    from repowise.core.report.render import report_renderers

    if fmt not in report_renderers.names:
        raise click.BadParameter(
            f"unknown format {fmt!r}; available: {', '.join(report_renderers.names)}",
            param_hint="--format",
        )
    return report_renderers.get(fmt)


def _emit(text: str, fmt: str, out_path: str | None, document: dict) -> None:
    """Write or print the rendered report, then warn about any failed section."""
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8", newline="\n")
        err_console.print(f"Wrote {fmt} report to {out_path}")
    else:
        click.echo(text, nl=False)
    failed = [
        f"{s['id']} ({s['repo']})" if s.get("repo") else s["id"]
        for s in document["sections"]
        if s.get("failed")
    ]
    if failed:
        from rich.markup import escape

        err_console.print(
            f"[yellow]warning[/yellow] {len(failed)} section(s) failed and are reported as "
            f"inconclusive: {escape(', '.join(failed))}"
        )


@click.command("assessment")
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
@click.option("--workspace", "workspace_flag", is_flag=True, help="Cover every workspace repo.")
@click.option("--no-workspace", is_flag=True, help="Force single-repo mode.")
@click.option("--repo", "repo_alias", default=None, help="Workspace repo alias to cover.")
def assessment_command(
    path: str | None,
    wanted: str | None,
    fmt: str,
    out_path: str | None,
    workspace_flag: bool,
    no_workspace: bool,
    repo_alias: str | None,
) -> None:
    """Assessment pack: components, hotspots, bus factor, dead code, test reach,
    contracts, decisions, provenance and the quarter's moves, each with its basis."""
    if not out_path:
        silence_logs_for_machine_output_until_close()  # stdout carries only the report
    sections = _pick_sections(wanted)
    render = _renderer(fmt)
    target = resolve_command_target(
        path=path,
        workspace_flag=workspace_flag,
        no_workspace_flag=no_workspace,
        repo_alias=repo_alias,
    )
    target.notice(err_console, command="report assessment")
    repos, scope = _scope(target)
    document = run_async(_build(repos, sections, scope, ws_root=target.ws_root))
    _emit(render(document), fmt, out_path, document)
