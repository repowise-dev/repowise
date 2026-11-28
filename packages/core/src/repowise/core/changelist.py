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
    "split_changelist",
    "without_changelist_number",
]

_TRAILER_RE = re.compile(
    r"(?:^|[ \t])[ \t]*\[(?:git-p4|p4-fusion): (depot-paths? = [^\]\n]*)\][ \t\r]*$",
    re.MULTILINE,
)
#: A changelist is a positive 32-bit number in Perforce: ten digits at most.
_CHANGE_RE = re.compile(r": change = ([0-9]{1,10})(?=:|$)")
_CHANGELIST_RE = re.compile(r"[0-9]{1,10}")


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


def split_changelist(subject: str, body: str) -> tuple[str, str, str | None]:
    """``(subject, body, changelist)``: the number read first, then the trailer cut from both.

    Where a commit is parsed, so stored and prompted text never carries the
    trailer while the change is still named by its changelist.
    """
    number = changelist_number(f"{subject}\n{body}")
    return without_changelist_number(subject), without_changelist_number(body), number


def commit_for_changelist(repo_path: str | Path, number: str) -> str | None:
    """The newest commit whose mirror trailer names changelist *number*, or ``None``.

    Git errors and timeouts propagate: a search that did not finish is not a miss.
    """
    number = str(int(number))
    pattern = rf"^[[:blank:]]*\[(git-p4|p4-fusion): depot-paths? = .*: change = {number}(:|\])"
    proc = run_git(["log", "--all", "-1", "--format=%H", "-E", f"--grep={pattern}"], repo_path)
    sha = proc.stdout.strip() if proc.returncode == 0 else ""
    return sha or None


def resolve_revspec(repo_path: str | Path, revspec: str) -> str:
    """*revspec* with each changelist-shaped revision read as a changelist, else a commit.

    A changelist found in a mirror trailer becomes its commit's sha; digits no
    trailer names stay as given when git resolves them to a commit. Anything
    else is left for git to read. Raises :class:`RevisionError` for digits that
    name neither, or when the history search fails.
    """
    parts = split_revspec(revspec)
    if parts is None:
        return _resolve(repo_path, revspec)
    base, sep, head = parts
    if not (_CHANGELIST_RE.fullmatch(base) or _CHANGELIST_RE.fullmatch(head)):
        return revspec
    return f"{_resolve(repo_path, base)}{sep}{_resolve(repo_path, head)}"


def _resolve(repo_path: str | Path, rev: str) -> str:
    if not _CHANGELIST_RE.fullmatch(rev):
        return rev
    try:
        sha = commit_for_changelist(repo_path, rev)
        if sha:
            return sha
        proc = run_git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}"],
            repo_path,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RevisionError(
            f"Could not search the history for Perforce changelist {rev}: {exc}"
        ) from exc
    if proc.returncode == 0:
        return rev
    raise RevisionError(
        f"{rev!r} names no commit: no commit message carries Perforce changelist {rev} "
        "(the git-p4 or p4-fusion trailer) and git reads it as no single commit."
    )
