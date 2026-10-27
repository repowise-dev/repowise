"""The ``repowise risk`` Click command."""

from __future__ import annotations

import click

from repowise.cli.ci import CI_FORMATS, EXIT_GATE_FAILED
from repowise.cli.commands import _tool_adapters as _ta
from repowise.cli.output import format_option, full_option

from .change import _resolve_subject, _score
from .change_render import _emit_machine, _print_change
from .target import _target_risk


@click.command("risk")
@click.argument("revspec", required=False, default=None)
@click.option(
    "--path",
    "repo_path",
    default=".",
    type=click.Path(exists=True, file_okay=False),
    help="Path to the git repository (default: current directory).",
)
@click.option(
    "--ext",
    "ext",
    default=None,
    help="Comma-separated file suffixes to count (e.g. '.py' or '.ts,.tsx'). "
    "Default: count every changed file.",
)
@click.option(
    "--baseline",
    "baseline",
    default=200,
    type=click.IntRange(min=0),
    help="Sample this many recent commits to rank the change within the repo "
    "(0 disables; shows only the absolute per-commit model-score band).",
)
@click.option(
    "--exclude",
    "-x",
    "exclude",
    multiple=True,
    metavar="PATTERN",
    help="Gitignore-style pattern to exclude. Repeatable; also applies to the baseline.",
)
@click.option(
    "--target",
    "-t",
    "targets",
    multiple=True,
    metavar="PATH",
    help="Score what history says about these FILES instead of a change. "
    "Repeatable. Reads the index, so the repo must be indexed.",
)
@click.option(
    "--changed-file",
    "changed_files",
    multiple=True,
    metavar="PATH",
    help="With --target: PR mode. The response leads with a directive naming "
    "structural review candidates, missing co-changes/tests, and what to run.",
)
@click.option(
    "--fail-above-percentile",
    "fail_above",
    type=click.FloatRange(0, 100),
    default=None,
    help="CI gate: exit 1 when the change ranks above this percentile of recent "
    "commits (risk_percentile, never the 0-10 score); exit 2 when it cannot be "
    "ranked. Without REVSPEC it scores the CI change against its target branch.",
)
@format_option(
    choices=CI_FORMATS,
    help="Output format. ``markdown`` and ``github`` are not for --target, and "
    "without REVSPEC they score the CI change against its target branch; "
    "``github`` writes annotations to stdout and the markdown summary to "
    "$GITHUB_STEP_SUMMARY when set.",
)
@full_option()
def risk_command(
    revspec: str | None,
    repo_path: str,
    ext: str | None,
    baseline: int,
    exclude: tuple[str, ...],
    targets: tuple[str, ...],
    changed_files: tuple[str, ...],
    fail_above: float | None,
    fmt: str,
    full: bool,
) -> None:
    """Rank a change by size and spread, or report the history of named files.

    With no --target this scores REVSPEC (a commit, or a ``base..head``
    range) from its diff. Omit REVSPEC to score your uncommitted work, or
    HEAD when the tree is clean; with --fail-above-percentile, or a
    markdown or github format, it scores the CI change instead (the pull
    request's target branch...HEAD). With
    --target it reports what the repo's history says about the named files,
    the same as the get_risk MCP tool.
    """
    if targets:
        if fmt not in ("table", "json"):
            raise click.UsageError("--target supports --format table or json.")
        if fail_above is not None:
            raise click.UsageError("--fail-above-percentile gates a change, not --target files.")
        _target_risk(repo_path, targets, changed_files, fmt, full)
        return
    if changed_files:
        raise click.UsageError("--changed-file needs at least one --target.")
    # ``--full`` means the complete payload as JSON on every command; the
    # REVSPEC path has a full payload too, so the flag is honoured here.
    fmt = _ta.resolve_format_for(fmt, full)
    subject = _resolve_subject(repo_path, revspec, fmt, fail_above)
    extensions = tuple(e.strip() for e in ext.split(",")) if ext else ()
    result = _score(subject, extensions, exclude, baseline)
    failed = fail_above is not None and result.percentile > fail_above
    if fmt == "table":
        _print_change(subject, result, failed)
    else:
        _emit_machine(subject, result)
    if failed:
        raise click.exceptions.Exit(EXIT_GATE_FAILED)
