"""Perforce changelist numbers in a git mirror of a depot.

git-p4 and p4-fusion end every commit they import with one line naming the
changelist it came from::

    [git-p4: depot-paths = "//depot/main/": change = 12345]
    [git-p4: depot-paths = "//depot/main/": change = 12345: options = ktext]
    [p4-fusion: depot-paths = "//depot/main/": change = 12345]

p4-fusion also starts the subject with ``12345 - ``. The line ends the
message; after a one-line description with no blank line, git reads it into
the subject, joined by a space. The team names a change
by that number, so it is read from the message, shown beside the sha, and
accepted where a revision is.

A revision that is all digits is ambiguous: every digit is hex, so git reads
``1234567`` as an abbreviated commit id and may land on an unrelated commit.
:func:`resolve_revspec` reads such a revision as a changelist first, then as
a commit, and refuses it when neither names one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from repowise.core.analysis.git_cli import run_git, split_revspec

__all__ = [
    "RevisionError",
    "changelist_number",
    "commit_for_changelist",
    "resolve_revspec",
    "without_changelist_number",
]

_TRAILER_RE = re.compile(
    r"(?:^|[ \t])[ \t]*\[(?:git-p4|p4-fusion): ([^\]\n]*)\][ \t]*$", re.MULTILINE
)
_CHANGE_RE = re.compile(r"(?:^|: )change = (\d+)(?=:|$)")
_DIGITS_RE = re.compile(r"[0-9]+")


class RevisionError(ValueError):
    """A revision the caller gave names no commit; the message says why."""


def changelist_number(message: str) -> str | None:
    """The changelist a mirror's trailer in *message* names, or ``None``."""
    found = None
    for m in _TRAILER_RE.finditer(message or ""):
        if change := _CHANGE_RE.search(m.group(1)):
            found = str(int(change.group(1)))
    return found


def without_changelist_number(text: str, number: str | None = None) -> str:
    """*text* less the mirror trailer and, given *number*, a ``<number> - `` lead."""
    text = _TRAILER_RE.sub("", text or "").strip()
    if number and text.startswith(f"{number} - "):
        text = text[len(number) + 3 :]
    return text


def commit_for_changelist(repo_path: str | Path, number: str) -> str | None:
    """The newest commit whose mirror trailer names changelist *number*, or ``None``."""
    number = str(int(number))
    pattern = rf"^[ \t]*\[(git-p4|p4-fusion): .*change = {number}(:|\])"
    try:
        proc = run_git(["log", "--all", "-1", "--format=%H", "-E", f"--grep={pattern}"], repo_path)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = proc.stdout.strip() if proc.returncode == 0 else ""
    return sha or None


def resolve_revspec(repo_path: str | Path, revspec: str) -> str:
    """*revspec* with each all-digit revision read as a changelist, else a commit.

    A changelist found in a mirror trailer becomes its commit's sha; digits no
    trailer names stay as given when git resolves them to a commit. Anything
    else is left for git to read. Raises :class:`RevisionError` for digits that
    name neither.
    """
    parts = split_revspec(revspec)
    if parts is None:
        return _resolve(repo_path, revspec)
    base, sep, head = parts
    if not (_DIGITS_RE.fullmatch(base) or _DIGITS_RE.fullmatch(head)):
        return revspec
    return f"{_resolve(repo_path, base)}{sep}{_resolve(repo_path, head)}"


def _resolve(repo_path: str | Path, rev: str) -> str:
    if not _DIGITS_RE.fullmatch(rev):
        return rev
    sha = commit_for_changelist(repo_path, rev)
    if sha:
        return sha
    try:
        proc = run_git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}"],
            repo_path,
        )
    except (OSError, subprocess.SubprocessError):
        return rev
    if proc.returncode == 0:
        return rev
    raise RevisionError(
        f"{rev!r} names no commit: no commit message carries Perforce changelist {rev} "
        "(the git-p4 or p4-fusion trailer) and git reads it as no single commit."
    )
