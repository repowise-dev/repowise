"""``repowise coverage check``: gate a change on its patch coverage, in CI.

Needs git and a coverage report, nothing else: no index, no API key. Report
paths are resolved against ``git ls-files``, so a file added by the change
resolves too (an index cached from the base branch would not know it).

Exit codes: 0 when the gate passes or there is nothing to judge, 1 when patch
coverage is below ``--fail-under``, 2 when the check could not run (no report,
unreadable report, unknown revision, missing history, bad config).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

import click
from rich.markup import escape

from repowise.cli.helpers import console, err_console
from repowise.cli.output import emit_json, format_option
from repowise.core.analysis.health.coverage import PARSERS as COVERAGE_PARSERS

#: Exit status when the check could not be evaluated at all.
EXIT_CANNOT_EVALUATE = 2

#: CI variables naming the branch (or commit) a change will merge into, in the
#: order they are consulted. A branch name is read from the ``origin`` remote.
_CI_BASE_VARS = (
    ("GITHUB_BASE_REF", "branch"),  # GitHub Actions pull_request
    ("CI_MERGE_REQUEST_DIFF_BASE_SHA", "commit"),  # GitLab merge request
    ("CHANGE_TARGET", "branch"),  # Jenkins multibranch pull request
    ("BITBUCKET_PR_DESTINATION_BRANCH", "branch"),  # Bitbucket Pipelines
)


class _CannotEvaluateError(Exception):
    """The check could not run; the message says what to do instead."""


@click.command("check")
@click.argument("revspec", required=False, default=None)
@click.option(
    "--report",
    "reports",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Coverage report to read (repeatable; merged hit-wins). "
    "Defaults to coverage.paths in .repowise/config.yaml, else discovery.",
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
@click.option(
    "--path",
    "repo",
    default=None,
    help="A path inside the repository (defaults to cwd). Config is read at the repo root.",
)
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
    ran. REVSPEC is the change: ``origin/main...HEAD`` (what the branch did
    since it forked, the pull-request view), ``base..head``, or one commit.
    Without it, the base comes from the CI's pull-request variables (GitHub,
    GitLab, Jenkins, Bitbucket), else the remote's default branch.

    Examples:

        repowise coverage check origin/main...HEAD --report coverage/lcov.info --fail-under 80
        repowise coverage check --report coverage.out --format github
        repowise coverage check HEAD --format json
    """
    # Markdown and annotations are machine output too, so asides go to stderr
    # for every format but the table (``notice_console`` only moves them for json).
    notices = console if fmt == "table" else err_console
    try:
        pc = _evaluate(revspec, reports, report_format, fail_under, repo, notices)
    except _CannotEvaluateError as exc:
        _fail(fmt, str(exc))
    _emit(pc, fmt)
    if pc.gate == "fail":
        raise click.exceptions.Exit(1)


def _evaluate(revspec, reports, report_format, fail_under, repo, notices):
    from repowise.core import git_refs
    from repowise.core.analysis.health.coverage import build_coverage_map
    from repowise.core.analysis.patch_coverage import patch_coverage_from_resolved

    root = _repo_root(repo)
    cfg = _coverage_config(root)
    threshold = fail_under if fail_under is not None else cfg.fail_under
    report_paths = [Path(p) for p in reports] or cfg.report_paths(root)
    if not report_paths:
        raise _CannotEvaluateError(
            "No coverage report found. Pass one with --report (lcov.info, coverage.xml, "
            "coverage.out, jacoco.xml, ...) or set coverage.paths in .repowise/config.yaml."
        )
    if notices is console:
        # Machine formats carry the list in ``scope.reports`` instead.
        notices.print(f"[dim]Reading {', '.join(escape(str(p)) for p in report_paths)}[/dim]")

    changed, label = _changed_lines(str(root), revspec or _default_revspec(str(root)))
    resolved, errors = build_coverage_map(
        root,
        report_paths,
        set(git_refs.tracked_paths(str(root))),
        coverage_format=report_format or cfg.format,
        strip_prefix=cfg.strip_prefix,
        path_prefix=cfg.path_prefix,
    )
    for path, err in errors:
        notices.print(f"[yellow]{escape(path.name)}: {escape(err)}[/yellow]")
    if len(errors) == len(report_paths):
        raise _CannotEvaluateError("No coverage report could be read; see the messages above.")
    if not resolved.files:
        raise _CannotEvaluateError(
            "No report path matched a file in this repository. If the report paths "
            "carry a build prefix, set coverage.strip_prefix in .repowise/config.yaml."
        )
    if resolved.mapping_partial:
        notices.print(
            "[yellow]More than half the report paths did not match a file in this "
            "repository; check coverage.strip_prefix / coverage.path_prefix.[/yellow]"
        )
    return patch_coverage_from_resolved(
        changed,
        resolved,
        threshold=threshold,
        label=label,
        reports=[str(p) for p in report_paths],
    )


def _repo_root(repo: str | None) -> Path:
    from repowise.core import git_refs

    root = git_refs.toplevel(str(Path(repo or ".").resolve()))
    if not root:
        raise _CannotEvaluateError("Not a git repository (or git is not installed).")
    return Path(root)


def _coverage_config(root: Path):
    from repowise.core.analysis.health.coverage import CoverageConfig
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    try:
        raw = load_repo_config(root)
    except RepoConfigError as exc:
        raise _CannotEvaluateError(str(exc)) from exc
    cfg = CoverageConfig.from_repo_config(raw)
    block = raw.get("coverage")
    if isinstance(block, dict) and block.get("fail_under") is not None and cfg.fail_under is None:
        # A gate that silently stops gating is worse than no gate.
        raise _CannotEvaluateError(
            f"coverage.fail_under must be a number from 0 to 100, got {block['fail_under']!r}."
        )
    return cfg


def _default_revspec(root: str) -> str:
    """``<base>...HEAD`` from the CI's pull-request variables, else the default branch."""
    from repowise.core import git_refs

    for var, kind in _CI_BASE_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return f"{value if kind == 'commit' else f'origin/{value}'}...HEAD"
    base = git_refs.default_base(root)
    if base == "HEAD":
        # CI checkouts rarely set origin/HEAD and have no local trunk branch.
        base = next((b for b in ("origin/main", "origin/master") if git_refs.resolve(root, b)), "")
    if not base:
        raise _CannotEvaluateError(
            "Could not tell which branch this change targets. Pass REVSPEC, "
            "e.g. origin/main...HEAD."
        )
    return f"{base}...HEAD"


def _changed_lines(root: str, revspec: str) -> tuple[dict[str, set[int]], str]:
    import subprocess

    from repowise.core.analysis.changed_lines import changed_lines

    try:
        return changed_lines(root, revspec)
    except ValueError as exc:
        raise _CannotEvaluateError(
            f"Could not diff {revspec}: {exc}. A shallow CI clone needs the base "
            "branch and enough history for a merge-base (fetch-depth: 0, or "
            "git fetch --deepen)."
        ) from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise _CannotEvaluateError(f"Could not run git: {exc}") from exc


def _fail(fmt: str, message: str) -> NoReturn:
    if fmt == "github":
        click.echo(f"::error::{message}")
    err_console.print(f"[red]{escape(message)}[/red]")
    raise click.exceptions.Exit(EXIT_CANNOT_EVALUATE)


def _emit(pc, fmt: str) -> None:
    from repowise.core.analysis.patch_coverage import github_annotations, render_markdown

    if fmt == "json":
        emit_json(pc.to_dict())
    elif fmt == "markdown":
        click.echo(render_markdown(pc), nl=False)
    elif fmt == "github":
        for line in github_annotations(pc):
            click.echo(line)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(render_markdown(pc))
        _print_summary(pc)
    else:
        _print_summary(pc)
        _print_table(pc)


def _print_summary(pc) -> None:
    from repowise.core.analysis.patch_coverage import headline, scope_line

    console.print(escape(headline(pc, markdown=False)))
    console.print(f"[dim]{escape(scope_line(pc, markdown=False))}[/dim]")


def _print_table(pc) -> None:
    from rich.table import Table

    from repowise.core.analysis.patch_coverage import (
        RANGE_LIMIT,
        STATUS_TEXT,
        attention_rows,
        format_ranges,
    )

    rows = attention_rows(pc)
    if not rows:
        return
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("File")
    table.add_column("Covered", justify="right")
    table.add_column("Uncovered changed lines")
    for f in rows:
        if f.status == "measured":
            covered = f"{f.covered_line_count} of {f.coverable_line_count}"
            gaps = format_ranges(f.uncovered_ranges, RANGE_LIMIT)
        else:
            covered, gaps = "", STATUS_TEXT[f.status]
        table.add_row(escape(f.file_path), covered, gaps)
    console.print(table)
