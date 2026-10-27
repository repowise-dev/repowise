"""The CLI side of CI gates: exit codes, output channels, the step summary.

Every gating command (``coverage check``, ``doc-drift --check``, ...) exits
with the same codes and writes to the same channels, so a pipeline treats them
alike: ``1`` means the gate failed, ``2`` means it could not be evaluated
(broken setup, not broken code). Content rendering stays with each feature;
the workflow-command strings come from :mod:`repowise.core.ci.github`.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator, Mapping
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


def ci_revspec(root: str, revspec: str | None) -> str:
    """*revspec*, else ``<CI target branch>...HEAD``: the change a gate judges."""
    from repowise.core.ci.base import BaseNotFoundError, default_revspec

    try:
        return revspec or default_revspec(root)
    except BaseNotFoundError as exc:
        raise CannotEvaluateError("base_not_found", str(exc)) from exc


def change_lines(
    root: str, revspec: str | None, *, staged: bool = False
) -> tuple[dict[str, set[int]], str]:
    """``({file: new-side lines}, label)`` for *revspec*, else the CI's target branch.

    With *staged*, the staged diff (what ``git commit`` would record) instead.
    """
    from repowise.core.analysis.changed_lines import changed_lines

    revspec = None if staged else ci_revspec(root, revspec)
    with _reading_change(revspec or "the staged changes", shallow_hint=not staged):
        return changed_lines(root, revspec, staged=staged)


def change_set(root: str, revspec: str | None, *, staged: bool = False) -> Any:
    """Every path *revspec* touches, else the staged changes (a ``ChangeSet``)."""
    from repowise.core.analysis.changed_lines import change_set as read_change_set

    with _reading_change(revspec or "the staged changes", shallow_hint=not staged):
        return read_change_set(root, revspec, staged=staged)


def baseline_growth(root: Path, path: Path, revspec: str | None) -> list[dict]:
    """The entries the baseline at *path* adds over the same file at the change's base.

    The base is the commit *revspec*'s diff starts from, or ``HEAD`` for staged
    changes (``None``). This reads both sides from the checkout and git; the
    comparison is :func:`repowise.core.ci.baseline.baseline_growth`.
    """
    import subprocess

    from repowise.core import git_refs
    from repowise.core.analysis.git_cat_file import cat_file_batch
    from repowise.core.ci.baseline import BaselineError
    from repowise.core.ci.baseline import baseline_growth as growth_between

    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise CannotEvaluateError(
            "baseline_outside_repository",
            f"Baseline growth is read from git history, and {path} is outside the "
            f"repository at {root}.",
        ) from None
    if revspec is None:
        base = git_refs.resolve(str(root), "HEAD")
    else:
        base = git_refs.change_base(str(root), revspec)
    if not base:
        raise CannotEvaluateError(
            "base_not_found",
            f"Could not find the base of {revspec or 'the staged changes'} to compare "
            f"the baseline with. {SHALLOW_CLONE_HINT}",
        )
    try:
        # A resolved commit, so a miss is the file the base lacks, not an unknown rev.
        # ``./`` resolves against *root*, which may sit below the git toplevel.
        read = cat_file_batch(str(root), [f"{base}:./{relative}"], check=True)
        head = path.read_bytes()
    except (subprocess.SubprocessError, OSError) as exc:
        raise CannotEvaluateError("git_failed", f"Could not read the baseline: {exc}") from exc
    try:
        return growth_between(read[0][1] if read else None, head, name=str(path))
    except BaselineError as exc:
        raise CannotEvaluateError("baseline_unreadable", str(exc)) from exc


def report_baseline_growth(fmt: str, path: Path | None, added: list[dict] | None) -> None:
    """Say which entries *path* gained, if any; the caller exits :data:`EXIT_GATE_FAILED`.

    ``json`` carries them in its own document; ``github`` adds an ``::error::``.
    """
    from rich.markup import escape

    from repowise.core.ci.github import error

    if fmt == "json" or not added:
        return
    noun = "entry" if len(added) == 1 else "entries"
    header = (
        f"Baseline grew: {path} lists {len(added)} {noun} its base does not. A baseline "
        "only shrinks; fix the new findings instead of accepting them."
    )
    rows = [
        " ".join(str(e[k]) for k in ("file_path", "kind", "target") if e.get(k))
        or e["fingerprint"]
        for e in added
    ]
    if fmt == "github":
        click.echo(error(f"{header} Added: {'; '.join(rows)}"))
    notices = ci_notices(fmt)
    notices.print(f"[red]{escape(header)}[/red]")
    for row in rows:
        notices.print(f"  {escape(row)}")


@contextlib.contextmanager
def _reading_change(what: str, *, shallow_hint: bool = True) -> Iterator[None]:
    """Turn a failed diff of *what* into :class:`CannotEvaluateError`."""
    import subprocess

    try:
        yield
    except ValueError as exc:
        hint = f" {SHALLOW_CLONE_HINT}" if shallow_hint else ""
        raise CannotEvaluateError(
            "diff_failed", f"Could not diff {what}: {exc}.{hint}"
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


#: Set by the GitHub Action: a directory where each gate run in ``github``
#: format leaves its row of the combined summary (``repowise ci-summary``).
GATE_SUMMARY_DIR_ENV = "REPOWISE_GATE_SUMMARY_DIR"


def write_gate_summary(summary: Any, *, env: Mapping[str, str] | None = None) -> bool:
    """Write *summary* (a ``GateSummary``) to ``$REPOWISE_GATE_SUMMARY_DIR/<gate>.json``.

    ``False`` when the variable is unset or the file cannot be written; the
    combined summary then reads the gate as not evaluated, never as passed.
    """
    import json

    target = (os.environ if env is None else env).get(GATE_SUMMARY_DIR_ENV)
    if not target:
        return False
    try:
        Path(target).mkdir(parents=True, exist_ok=True)
        Path(target, f"{summary.gate}.json").write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        return False
    return True
