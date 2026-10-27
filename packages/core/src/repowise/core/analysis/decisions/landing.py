"""Where a decision's own evidence commits reached the main line.

A decision recorded on a branch names that branch's commits, and the squash
that lands them comes later. Staleness counts only commits after the landing,
so the commit that carried the decision is not read as drift from it.

A commit on HEAD's first-parent line landed as itself. A branch commit landed
in the one first-parent commit after it that names it: its subject, less a
trailing ``(#N)``, is the commit's subject (a squash titled after it), or its
body lists ``* <subject>`` (the squash body GitHub writes), and it changes a
file the commit changed, so a generic subject cannot match an unrelated change
by name alone. Anything else,
including a commit merged by a merge commit or a name two commits share, is
left unresolved and its record keeps its creation time as the anchor, so an
unresolved commit only ever leaves a record as stale as it was.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from repowise.core.analysis.git_cli import run_git

logger = structlog.get_logger(__name__)

_SHA = re.compile(r"[0-9a-f]{7,40}")
_FIELD = "\x1f"
_RECORD = "\x1e"
# Files follow the last field, from ``--name-only``.
_FORMAT = f"--format={_RECORD}%H{_FIELD}%ct{_FIELD}%s{_FIELD}%b{_FIELD}"


@dataclass(frozen=True)
class Landing:
    """The first-parent commit that carried an evidence commit, and its commit time."""

    sha: str
    at: datetime


@dataclass(frozen=True)
class _Commit:
    at: datetime
    subject: str
    body: str
    files: frozenset[str]


def evidence_landings(
    repo_path: Path | str, evidence: Iterable[str], *, timeout: float | None = None
) -> dict[str, Landing]:
    """Each stored evidence sha that can be placed on the main line, mapped to its landing.

    Three git calls for the whole set, each bounded by *timeout* when given.
    Values that are not shas (session ids) and commits git does not hold are
    skipped; a git failure gives ``{}``.
    """
    keys = {e.strip().lower() for e in evidence if isinstance(e, str)}
    wanted = sorted(k for k in keys if _SHA.fullmatch(k))
    if not wanted:
        return {}
    try:
        own = _commits(repo_path, wanted, timeout)
        line = _first_parent(repo_path, min(c.at for c in own.values()), timeout) if own else {}
    except (OSError, subprocess.SubprocessError) as exc:
        # One line: a git timeout here is routine on a large history, not a crash.
        logger.debug("decision_landing_unreadable", error=f"{type(exc).__name__}: {exc}")
        return {}
    names = _names(line)
    out: dict[str, Landing] = {}
    for stored, full in _expand(wanted, own).items():
        landing = _landing(full, own[full], line, names)
        if landing is not None:
            out[stored] = landing
    return out


def record_landing(
    evidence_commits: Iterable[str], landings: dict[str, Landing], born: datetime | None
) -> Landing | None:
    """The latest landing among a record's evidence commits, when it is after *born*."""
    from .staleness import _as_aware_utc

    keys = (e.strip().lower() for e in evidence_commits if isinstance(e, str))
    found = [landings[k] for k in keys if k in landings]
    if not found or born is None:
        return None
    latest = max(found, key=lambda landing: landing.at)
    return latest if latest.at > _as_aware_utc(born) else None


def _names(line: dict[str, _Commit]) -> dict[str, set[str]]:
    """Branch-commit subject -> the first-parent commits that name it."""
    from .reverts import _strip_pr_suffix

    names: dict[str, set[str]] = {}
    for sha, commit in line.items():
        names.setdefault(_strip_pr_suffix(commit.subject), set()).add(sha)
        for row in commit.body.splitlines():
            if row.startswith("* "):
                names.setdefault(row[2:].strip(), set()).add(sha)
    return names


def _landing(
    full: str, own: _Commit, line: dict[str, _Commit], names: dict[str, set[str]]
) -> Landing | None:
    if full in line:
        return Landing(full, line[full].at)
    later = [
        s
        for s in names.get(own.subject.strip(), ())
        if line[s].at >= own.at and line[s].files & own.files
    ]
    # Two candidates means the name does not identify the squash.
    if len(later) != 1:
        return None
    return Landing(later[0], line[later[0]].at)


def _expand(wanted: list[str], own: dict[str, _Commit]) -> dict[str, str]:
    """Stored (possibly short) sha -> the one full sha it names among *own*."""
    out: dict[str, str] = {}
    for stored in wanted:
        matches = [full for full in own if full.startswith(stored)]
        if len(matches) == 1:
            out[stored] = matches[0]
    return out


def _commits(
    repo_path: Path | str, wanted: list[str], timeout: float | None
) -> dict[str, _Commit]:
    """Each sha git holds as a commit; missing and ambiguous ones drop."""
    check = run_git(
        ["cat-file", "--batch-check=%(objectname) %(objecttype)"],
        repo_path,
        input="\n".join(wanted) + "\n",
        **_bound(timeout),
    )
    full = [
        parts[0]
        for parts in (row.split() for row in check.stdout.splitlines())
        if len(parts) == 2 and parts[1] == "commit"
    ]
    if not full:
        return {}
    shown = run_git(
        ["log", "--no-walk=unsorted", "--name-only", _FORMAT, *full], repo_path, **_bound(timeout)
    )
    return _parse(shown.stdout) if shown.returncode == 0 else {}


def _first_parent(
    repo_path: Path | str, since: datetime, timeout: float | None
) -> dict[str, _Commit]:
    shown = run_git(
        ["log", "--first-parent", "--name-only", f"--since={since.isoformat()}", _FORMAT, "HEAD"],
        repo_path,
        **_bound(timeout),
    )
    return _parse(shown.stdout) if shown.returncode == 0 else {}


def _bound(timeout: float | None) -> dict:
    """``run_git``'s own ceiling unless the caller bought a tighter one."""
    return {} if timeout is None else {"timeout": timeout}


def _parse(stdout: str) -> dict[str, _Commit]:
    out: dict[str, _Commit] = {}
    for record in stdout.split(_RECORD):
        parts = record.split(_FIELD, 4)
        if len(parts) == 5 and parts[1].isdigit():
            at = datetime.fromtimestamp(int(parts[1]), tz=UTC)
            files = frozenset(f.strip() for f in parts[4].splitlines() if f.strip())
            out[parts[0].strip()] = _Commit(at, parts[2].strip(), parts[3], files)
    return out
