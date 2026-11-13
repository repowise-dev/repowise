"""``repowise ci-summary``: one summary over the gates a CI job ran.

The GitHub Action runs it after the gates. Each gate run in ``github`` format
leaves its row in ``$REPOWISE_GATE_SUMMARY_DIR``; this reads the rows back,
reconciles each with the gate's exit code, appends the verdict table to the
job summary, writes the sticky pull-request comment and prints the combined
verdict (``pass``, ``warn`` or ``fail``). Rendering is
:mod:`repowise.core.ci.summary`.

In GitLab CI, ``--gitlab-note`` posts the same comment as one merge-request
note that later pipelines edit in place (:mod:`repowise.core.forges.gitlab_api`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
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


#: The access token that posts the merge-request note.
GITLAB_TOKEN_ENV = "REPOWISE_GITLAB_TOKEN"


def post_gitlab_note(
    rows, key: str = "", *, env: Mapping[str, str] | None = None, transport=None
) -> None:
    """Post *rows* as this pipeline's merge-request note, or edit the one an earlier
    pipeline posted. Says on stderr what it did; never raises, so the gates alone
    decide the job.
    """
    from repowise.core.ci.summary import comment_marker, render_comment
    from repowise.core.forges.gitlab_api import GitLabClient, GitLabError, find_note, upsert_note

    env = os.environ if env is None else env
    iid = env.get("CI_MERGE_REQUEST_IID", "")
    if not iid.isdigit():
        click.echo("Not a merge-request pipeline, so no merge-request note is posted.", err=True)
        return
    token = env.get(GITLAB_TOKEN_ENV)
    api_url, project = env.get("CI_API_V4_URL"), env.get("CI_PROJECT_ID")
    if not token or not api_url or not project:
        click.echo(
            f"No merge-request note: {GITLAB_TOKEN_ENV}, CI_API_V4_URL and CI_PROJECT_ID "
            "must all be set.",
            err=True,
        )
        return
    try:
        with GitLabClient(api_url, token, transport=transport) as client:
            existing = find_note(client, project, int(iid), comment_marker(key))
            previous = existing.get("body") if existing else None
            body = render_comment(rows, previous=previous, key=key)
            note, created = upsert_note(client, project, int(iid), body, existing=existing)
        note_id = note["id"]
    except GitLabError as exc:
        click.echo(f"Could not post the merge-request note. {exc}", err=True)
        return
    except (LookupError, TypeError, AttributeError) as exc:
        # A proxy or a page that is not the API can answer in the wrong shape.
        click.echo(f"Could not post the merge-request note: unexpected answer ({exc!r}).", err=True)
        return
    click.echo(f"{'Posted' if created else 'Updated'} merge-request note {note_id}.", err=True)


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
@click.option(
    "--gitlab-note",
    is_flag=True,
    help="In a GitLab merge-request pipeline, post the comment as one merge-request "
    f"note that later pipelines edit in place. Needs {GITLAB_TOKEN_ENV}.",
)
def ci_summary_command(
    summary_dir: Path,
    gates: tuple[str, ...],
    previous: Path | None,
    comment_file: Path | None,
    comment_key: str,
    gitlab_note: bool,
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
    if gitlab_note:
        post_gitlab_note(rows, comment_key)
    click.echo(combined_verdict(rows))
