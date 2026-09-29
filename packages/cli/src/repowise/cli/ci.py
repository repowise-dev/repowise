"""The CLI side of CI gates: exit codes, output channels, the step summary.

Every gating command (``coverage check``, ``doc-drift --check``, ...) exits
with the same codes and writes to the same channels, so a pipeline treats them
alike: ``1`` means the gate failed, ``2`` means it could not be evaluated
(broken setup, not broken code). Content rendering stays with each feature;
the workflow-command strings come from :mod:`repowise.core.ci.github`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

import click

#: The gate ran and the change failed it.
EXIT_GATE_FAILED = 1
#: The gate could not run: no report, unknown revision, bad config, ...
EXIT_CANNOT_EVALUATE = 2

#: Output formats every gating command offers; a command may add more (SARIF,
#: GitLab Code Quality).
CI_FORMATS = ("table", "json", "markdown", "github")

#: Why a diff against the base failed in CI, nearly always.
SHALLOW_CLONE_HINT = (
    "A shallow CI clone needs the base branch and enough history for a "
    "merge-base (fetch-depth: 0, or git fetch --deepen)."
)


def ci_notices(fmt: str) -> Any:
    """Where asides go: stdout for the table, stderr for every machine format."""
    from repowise.cli.helpers import console, err_console

    return console if fmt == "table" else err_console


def cannot_evaluate(fmt: str, code: str, message: str) -> NoReturn:
    """Report why the gate could not run and exit :data:`EXIT_CANNOT_EVALUATE`.

    ``json`` gets a ``{"error", "message"}`` document on stdout, ``github`` an
    ``::error::`` line, ``gitlab`` an empty issue list so the report artifact
    stays valid JSON; the message always reaches stderr for a human.
    """
    from rich.markup import escape

    from repowise.cli.helpers import err_console
    from repowise.cli.output import emit_json
    from repowise.core.ci.github import error

    if fmt == "json":
        emit_json({"error": code, "message": message})
    elif fmt == "github":
        click.echo(error(message))
    elif fmt == "gitlab":
        click.echo("[]")
    if fmt != "json":
        err_console.print(f"[red]{escape(message)}[/red]")
    raise click.exceptions.Exit(EXIT_CANNOT_EVALUATE)


class CannotEvaluateError(Exception):
    """The check could not run; *code* names why, the message what to do instead."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def repo_root(path: str | None) -> Path:
    """The git toplevel holding *path* (default: cwd)."""
    from repowise.core import git_refs

    root = git_refs.toplevel(str(Path(path or ".").resolve()))
    if not root:
        raise CannotEvaluateError(
            "not_a_git_repository", "Not a git repository (or git is not installed)."
        )
    return Path(root)


def change_lines(root: str, revspec: str | None) -> tuple[dict[str, set[int]], str]:
    """``({file: new-side lines}, label)`` for *revspec*, else the CI's target branch."""
    import subprocess

    from repowise.core.analysis.changed_lines import changed_lines
    from repowise.core.ci.base import BaseNotFoundError, default_revspec

    try:
        revspec = revspec or default_revspec(root)
    except BaseNotFoundError as exc:
        raise CannotEvaluateError("base_not_found", str(exc)) from exc
    try:
        return changed_lines(root, revspec)
    except ValueError as exc:
        raise CannotEvaluateError(
            "diff_failed",
            f"Could not diff {revspec}: {exc}. {SHALLOW_CLONE_HINT}"
        ) from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise CannotEvaluateError("git_failed", f"Could not run git: {exc}") from exc


def append_step_summary(markdown: str, *, env: Mapping[str, str] | None = None) -> bool:
    """Add *markdown* to the GitHub Actions job summary; ``False`` when not in Actions."""
    target = (os.environ if env is None else env).get("GITHUB_STEP_SUMMARY")
    if not target:
        return False
    try:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(markdown.rstrip("\n") + "\n")
    except OSError:
        return False
    return True
