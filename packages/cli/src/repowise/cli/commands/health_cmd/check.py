"""``repowise health check``: gate a change on the health findings it introduces.

Git only, no index and no database: both sides of the change are re-analysed
from git blobs (changed files only) and compared. Exit codes are the shared CI
ones (:mod:`repowise.cli.ci`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import click
from rich.markup import escape

from repowise.cli._setup import configure_cli_logging
from repowise.cli.ci import (
    CI_FORMATS,
    EXIT_GATE_FAILED,
    SHALLOW_CLONE_HINT,
    CannotEvaluateError,
    append_step_summary,
    cannot_evaluate,
    ci_revspec,
    repo_root,
)
from repowise.cli.helpers import console, silence_logs_for_machine_output_until_close
from repowise.cli.output import emit_json, format_option

if TYPE_CHECKING:
    from repowise.core.analysis.change_health.models import ChangeHealthDelta
    from repowise.core.analysis.health_gate import GateResult

_FORMATS = (*CI_FORMATS, "sarif", "gitlab")


@click.command("check")
@click.argument("revspec", required=False, default=None)
@click.option(
    "--fail-on",
    type=click.Choice(["introduced", "worsened"]),
    default="introduced",
    show_default=True,
    help="Exit 1 on findings of this kind or above: introduced only, or worsened too. "
    "Below it they warn.",
)
@click.option(
    "--path",
    "repo",
    default=None,
    help="A path inside the repository (defaults to cwd).",
)
@format_option(
    choices=_FORMATS,
    help="Output format. ``github`` writes annotations to stdout and the markdown "
    "summary to $GITHUB_STEP_SUMMARY when set; ``sarif`` is for code-scanning "
    "upload; ``gitlab`` is a GitLab Code Quality report.",
)
def health_check(revspec: str | None, fail_on: str, repo: str | None, fmt: str) -> None:
    """Gate a change on the health findings it introduces (no index needed, built for CI).

    Analyses the changed files at the change's base and head and compares the
    findings: one the change wrote fails, one it made worse warns, one it only
    moved or renamed never fails. Only code-shape findings (complexity,
    nesting, size, duplication, error handling) decide; git history, coverage
    and file scores never do.

    REVSPEC is the change: ``origin/main...HEAD`` (the pull-request view),
    ``base..head``, or one commit. Without it, the base comes from the CI's
    pull-request variables, else the remote's default branch.

    Examples:

        repowise health check origin/main...HEAD
        repowise health check --fail-on worsened
        repowise health check --format github
        repowise health check --format sarif > health.sarif
        repowise health check --format gitlab > gl-code-quality-health.json
    """
    if fmt != "table":
        silence_logs_for_machine_output_until_close()
    else:
        configure_cli_logging(verbose=False)
    try:
        verdict = _evaluate(revspec, fail_on, repo)
    except (click.exceptions.Exit, click.ClickException, click.Abort):
        raise
    except CannotEvaluateError as exc:
        cannot_evaluate(fmt, exc.code, str(exc))
    except Exception as exc:
        # A crash must never read as a failed gate.
        cannot_evaluate(fmt, "internal_error", f"health check failed unexpectedly: {exc!r}")
    _EMITTERS.get(fmt, _print_table)(verdict)
    if not verdict.result.passed:
        raise click.exceptions.Exit(EXIT_GATE_FAILED)


@dataclass(frozen=True)
class _Verdict:
    """A finished check, ready to render."""

    result: GateResult
    delta: ChangeHealthDelta
    label: str


def _evaluate(revspec: str | None, fail_on: str, repo: str | None) -> _Verdict:
    from repowise.core.analysis.change_health.service import (
        ChangeHealthDeltaService,
        DeltaRequest,
    )
    from repowise.core.analysis.health_gate import NotEvaluatedError, evaluate

    root = str(repo_root(repo))
    label = ci_revspec(root, revspec)
    delta = ChangeHealthDeltaService(repo_path=root).compare(DeltaRequest(root, label))
    try:
        result = evaluate(delta, fail_on=fail_on)
    except NotEvaluatedError as exc:
        hint = f" {SHALLOW_CLONE_HINT}" if exc.code == "unsupported_range" else ""
        raise CannotEvaluateError(exc.code, f"{exc}{hint}") from exc
    return _Verdict(result, delta, label)


def _markdown(v: _Verdict) -> str:
    from repowise.core.analysis.health_gate import render_markdown, scope_line

    return render_markdown(v.result, label=v.label, scope=scope_line(v.delta))


def _emit_json(v: _Verdict) -> None:
    from repowise.core.analysis.health_gate import as_json

    emit_json(as_json(v.result, v.delta, label=v.label))


def _emit_sarif(v: _Verdict) -> None:
    from repowise.cli import __version__
    from repowise.core.analysis.health_gate import render_sarif

    emit_json(render_sarif(v.result, tool_version=__version__))


def _emit_gitlab(v: _Verdict) -> None:
    from repowise.core.analysis.health_gate import render_gitlab

    emit_json(render_gitlab(v.result))


def _emit_markdown(v: _Verdict) -> None:
    click.echo(_markdown(v), nl=False)


def _emit_github(v: _Verdict) -> None:
    from repowise.core.analysis.health_gate import github_annotations

    for line in github_annotations(v.result):
        click.echo(line)
    append_step_summary(_markdown(v))


_COLOUR = {"error": "red", "warning": "yellow", "note": "dim"}
_VERDICT_LINE = {
    "pass": "[green]Gate passed[/green]",
    "warn": "[yellow]Gate passed with warnings[/yellow]",
    "fail": "[red]Gate failed[/red]",
}


def _print_table(v: _Verdict) -> None:
    from rich.table import Table

    from repowise.core.analysis.health_gate import FINDINGS_BASIS, location, scope_line

    console.print(f"[bold]{escape(v.label)}[/bold]: {scope_line(v.delta)}")
    for note in v.result.notes:
        console.print(f"[yellow]{escape(note)}[/yellow]")
    if v.result.findings:
        table = Table(show_edge=False, pad_edge=False)
        for column in ("Level", "Where", "Finding"):
            table.add_column(column)
        for f in v.result.findings:
            colour = _COLOUR[f.level]
            table.add_row(f"[{colour}]{f.level}[/]", escape(location(f)), escape(f.message))
        console.print(table)
    errors = len(v.result.at("error"))
    tail = f": {errors} finding(s) fail" if errors else ""
    console.print(
        f"{_VERDICT_LINE[v.result.verdict]}{tail} [dim](fails on {v.result.fail_on} or above)[/dim]"
    )
    console.print(f"[dim]{escape(FINDINGS_BASIS)}[/dim]")


#: Writers per ``--format``; ``table`` (the default) is :func:`_print_table`.
_EMITTERS: dict[str, Callable[[_Verdict], None]] = {
    "json": _emit_json,
    "sarif": _emit_sarif,
    "gitlab": _emit_gitlab,
    "markdown": _emit_markdown,
    "github": _emit_github,
}
