"""``repowise coverage check`` — gate a change on its patch coverage, in CI.

Needs git and a coverage report, nothing else: no index, no API key. Report
paths are resolved against ``git ls-files``, so a file added by the change
resolves too (an index cached from the base branch would not know it).

Exit codes: 0 when the gate passes or there is nothing to judge, 1 when patch
coverage is below ``--fail-under``, 2 when the check could not run (no report,
unreadable report, unknown revision, not a git repository).
"""

from __future__ import annotations

import os
from pathlib import Path

import click

from repowise.cli.helpers import console, err_console
from repowise.cli.output import emit_json, format_option
from repowise.core.analysis.health.coverage import PARSERS as COVERAGE_PARSERS

#: Exit status when the check could not be evaluated at all.
EXIT_CANNOT_EVALUATE = 2


@click.command("check")
@click.argument("revspec", required=False, default=None)
@click.option(
    "--report",
    "reports",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Coverage report to read (repeatable; merged hit-wins). "
    "Discovered from the usual locations when omitted.",
)
@click.option(
    "--report-format",
    type=click.Choice(list(COVERAGE_PARSERS)),
    default=None,
    help="Force a report parser instead of detecting it from the content.",
)
@click.option(
    "--fail-under",
    type=click.FloatRange(0, 100),
    default=None,
    help="Exit 1 when patch coverage is below this percentage. "
    "Defaults to coverage.fail_under in .repowise/config.yaml.",
)
@click.option("--path", "repo", default=None, help="Repository path (defaults to cwd).")
@format_option(
    choices=("table", "json", "markdown", "github"),
    help="Output format. ``github`` writes annotations to stdout and the "
    "markdown summary to $GITHUB_STEP_SUMMARY when set.",
)
def coverage_check(
    revspec: str | None,
    reports: tuple[str, ...],
    report_format: str | None,
    fail_under: float | None,
    repo: str | None,
    fmt: str,
) -> None:
    """Gate a change on its patch coverage (no index needed, built for CI).

    Patch coverage is the share of the change's executable lines the tests
    ran. REVSPEC is the change: ``origin/main...HEAD`` (what the branch did since it
    forked, the pull-request view), ``base..head``, or one commit. Defaults to
    the default branch ``...HEAD``.

    Examples:

        repowise coverage check origin/main...HEAD --report coverage/lcov.info --fail-under 80
        repowise coverage check --format github          # in a GitHub Actions job
        repowise coverage check HEAD --format json
    """
    from repowise.core import git_refs
    from repowise.core.analysis.changed_lines import changed_lines
    from repowise.core.analysis.health.coverage import (
        CoverageConfig,
        build_coverage_map,
        discover_artifacts,
    )
    from repowise.core.analysis.patch_coverage import PatchScope, compute_patch_coverage
    from repowise.core.repo_config import load_repo_config

    notices = console if fmt == "table" else err_console

    def _fail(message: str) -> None:
        notices.print(f"[red]{message}[/red]")
        raise click.exceptions.Exit(EXIT_CANNOT_EVALUATE)

    root_str = git_refs.toplevel(str(Path(repo or ".").resolve()))
    if not root_str:
        _fail("Not a git repository: coverage check diffs a change, so it needs one.")
    root = Path(root_str)
    cfg = CoverageConfig.from_repo_config(load_repo_config(root))
    threshold = fail_under if fail_under is not None else cfg.fail_under

    report_paths = [Path(p) for p in reports] or discover_artifacts(
        root, globs=cfg.artifacts or None
    )
    if not report_paths:
        _fail(
            "No coverage report found. Pass one with --report, e.g. "
            "coverage/lcov.info, coverage.xml, coverage.out or jacoco.xml."
        )

    revspec = revspec or f"{git_refs.default_base(root_str)}...HEAD"
    try:
        changed, label = changed_lines(root_str, revspec)
    except ValueError as exc:
        _fail(f"{exc}. In a shallow CI clone, fetch the base branch first.")

    resolved, errors = build_coverage_map(
        root,
        report_paths,
        set(git_refs.tracked_paths(root_str)),
        coverage_format=report_format or cfg.format,
        strip_prefix=cfg.strip_prefix,
        path_prefix=cfg.path_prefix,
    )
    for path, err in errors:
        notices.print(f"[yellow]{path.name}: {err}[/yellow]")
    if not resolved.files:
        _fail(
            "No report file matched a file in this repository. If the report "
            "paths carry a build prefix, set coverage.strip_prefix in "
            ".repowise/config.yaml."
        )

    pc = compute_patch_coverage(
        changed,
        {fc.file_path: fc for fc in resolved.files},
        threshold=threshold,
        scope=PatchScope(
            label=label,
            report_formats=tuple(resolved.source_formats),
            report_files=resolved.matched + len(resolved.unmatched) + len(resolved.ambiguous),
            unmatched_report_paths=len(resolved.unmatched) + len(resolved.ambiguous),
        ),
    )
    _emit(pc, fmt)
    if pc.gate == "fail":
        raise click.exceptions.Exit(1)


def _emit(pc, fmt: str) -> None:
    from repowise.core.analysis.patch_coverage import (
        github_annotations,
        render_markdown,
    )

    if fmt == "json":
        emit_json(pc.to_dict())
        return
    if fmt == "markdown":
        click.echo(render_markdown(pc), nl=False)
        return
    if fmt == "github":
        for line in github_annotations(pc):
            click.echo(line)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(render_markdown(pc))
        click.echo(_plain(pc))
        return
    _print_table(pc)


def _plain(pc) -> str:
    from repowise.core.analysis.patch_coverage import headline

    return headline(pc).replace("**", "")


def _print_table(pc) -> None:
    from rich.table import Table

    console.print(_plain(pc))
    if pc.scope.label:
        console.print(f"[dim]{pc.scope.label} · {', '.join(pc.scope.report_formats)}[/dim]")
    rows = [
        f
        for f in pc.files
        if f.uncovered_lines or f.status in ("not_in_report", "no_line_data")
    ]
    if not rows:
        return
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("File")
    table.add_column("Covered", justify="right")
    table.add_column("Uncovered changed lines")
    for f in rows:
        if f.status == "measured":
            covered = f"{f.covered_lines} of {f.coverable_lines}"
            gaps = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in f.uncovered_ranges)
        else:
            covered, gaps = "—", _STATUS_TEXT[f.status]
        table.add_row(f.path, covered, gaps)
    console.print(table)


_STATUS_TEXT = {
    "not_in_report": "not in report",
    "no_line_data": "report has no line data",
}
