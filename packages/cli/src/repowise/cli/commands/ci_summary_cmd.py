"""``repowise ci-summary``: one summary over the gates a CI job ran.

The GitHub Action runs it after the gates. Each gate run in ``github`` format
leaves its row in ``$REPOWISE_GATE_SUMMARY_DIR``; this reads the rows back,
reconciles each with the gate's exit code, appends the verdict table to the
job summary, writes the sticky pull-request comment and prints the combined
verdict (``pass``, ``warn`` or ``fail``). Rendering is
:mod:`repowise.core.ci.summary`.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from repowise.cli.ci import append_step_summary


def _gate_exit(value: str) -> tuple[str, int]:
    name, _, code = value.partition("=")
    if not name or not code.isdigit():
        raise click.UsageError(f"--gate expects NAME=EXIT_CODE, got {value!r}")
    return name, int(code)


def _read(directory: Path, gate: str):
    """The row *gate* left, or ``None`` when it left none or an unreadable one."""
    from repowise.core.ci.summary import GateSummary

    try:
        doc = json.loads(Path(directory, f"{gate}.json").read_text(encoding="utf-8"))
        summary = GateSummary.from_dict(doc)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return summary if summary.gate == gate else None


@click.command("ci-summary")
@click.argument("summary_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "--gate",
    "gates",
    multiple=True,
    required=True,
    metavar="NAME=EXIT_CODE",
    help="A gate that ran and its exit code, in the order to list them.",
)
@click.option(
    "--previous",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="The comment this run replaces; findings it listed that are gone count as resolved.",
)
@click.option(
    "--comment-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the pull-request comment here.",
)
@click.option(
    "--comment-key",
    default="",
    help="Tells this comment apart from another run's on the same pull request "
    "(the Action passes its working directory).",
)
def ci_summary_command(
    summary_dir: Path,
    gates: tuple[str, ...],
    previous: Path | None,
    comment_file: Path | None,
    comment_key: str,
) -> None:
    """Combine the gates' results: job-summary table, PR comment, verdict on stdout."""
    from repowise.core.ci.summary import (
        combined_verdict,
        render_comment,
        render_job_summary,
        with_exit_code,
    )

    ran = [_gate_exit(g) for g in gates]
    rows = [with_exit_code(_read(summary_dir, name), name, code) for name, code in ran]
    append_step_summary(render_job_summary(rows))
    if comment_file is not None:
        earlier = None
        if previous is not None:
            try:
                earlier = previous.read_text(encoding="utf-8")
            except OSError:
                earlier = None
        body = render_comment(rows, previous=earlier, key=comment_key)
        comment_file.write_text(body, encoding="utf-8")
    click.echo(combined_verdict(rows))
