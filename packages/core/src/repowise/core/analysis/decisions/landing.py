"""Where a decision's own evidence commits reached the main line.

A decision recorded on a branch names that branch's commits, and the merge
that lands them comes later. Staleness counts only commits after the landing,
so the commit that carried the decision is not read as drift from it, and a
decision an agent recorded governs once its commits land on the default branch.

A commit on the line's first-parent history landed as itself. A commit a merge
commit brought in landed in the oldest first-parent merge it is reachable from.
A branch commit squashed or rebased landed in the one first-parent commit after
it that names it: its subject, less a trailing ``(#N)``, is the commit's subject
(a squash titled after it, or the rebased copy), or its body lists
``* <subject>`` (the squash body GitHub writes), and it changes a file the
commit changed, all of them, so a generic subject cannot match an unrelated
change by name alone. Anything else, including a name two commits share, is left unresolved
and its record keeps its creation time as the anchor, so an unresolved commit
only ever leaves a record as stale as it was, and never puts one in force.
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
    repo_path: Path | str,
    evidence: Iterable[str],
    *,
    timeout: float | None = None,
    ref: str = "HEAD",
) -> dict[str, Landing]:
    """Each stored evidence sha that can be placed on *ref*'s line, mapped to its landing.

    Three git calls for the whole set, a fourth only when a commit is not on
    the first-parent line itself, each bounded by *timeout* when given. Values
    that are not shas (session ids) and commits git does not hold are skipped;
    a git failure gives ``{}``.
    """
    return _resolve(repo_path, evidence, ref, timeout)[0]


@dataclass(frozen=True)
class MainLine:
    """Where a set of evidence commits stands against the default branch."""

    #: The ref read, e.g. ``origin/main``.
    ref: str
    #: Stored evidence values git holds as commits.
    held: frozenset[str]
    landings: dict[str, Landing]

    def landed(self, evidence: Iterable[str]) -> Landing | None:
        """The latest landing when every held commit in *evidence* landed, else ``None``.

        All of them, not any: a record citing an old main-line commit beside
        its own branch commit has not landed until the branch has.
        """
        keys = _held_keys(evidence, self.held)
        if not keys or any(k not in self.landings for k in keys):
            return None
        return max((self.landings[k] for k in keys), key=lambda landing: landing.at)

    def waiting(self, evidence: Iterable[str]) -> bool:
        """Whether *evidence* names a commit git holds that has not landed yet."""
        return bool(_held_keys(evidence, self.held)) and self.landed(evidence) is None


def main_line(
    repo_path: Path | str, evidence: Iterable[str], *, timeout: float | None = None
) -> MainLine | None:
    """*evidence* against the default branch, or ``None`` when git names none."""
    from repowise.core.git_refs import default_base

    ref = default_base(str(repo_path))
    # ``HEAD`` is the fallback for a repository with no trunk: the checked-out
    # branch would then count as the default and land its own commits.
    if ref == "HEAD":
        return None
    landings, held = _resolve(repo_path, evidence, ref, timeout)
    return MainLine(ref, held, landings)


def _held_keys(evidence: Iterable[str], held: frozenset[str]) -> set[str]:
    return {e.strip().lower() for e in evidence if isinstance(e, str)} & held


def _resolve(
    repo_path: Path | str, evidence: Iterable[str], ref: str, timeout: float | None
) -> tuple[dict[str, Landing], frozenset[str]]:
    """Landings on *ref*, and the stored values git holds as commits."""
    keys = {e.strip().lower() for e in evidence if isinstance(e, str)}
    wanted = sorted(k for k in keys if _SHA.fullmatch(k))
    if not wanted:
        return {}, frozenset()
    try:
        own = _commits(repo_path, wanted, timeout)
        since = min(c.at for c in own.values()) if own else None
        line = _first_parent(repo_path, since, ref, timeout) if since else {}
        expanded = _expand(wanted, own)
        off_line = [full for full in expanded.values() if full not in line]
        merged = _merged_in(repo_path, since, ref, line, timeout) if line and off_line else {}
        names = _names(line)
        # Files only for the commits a name points at: listing them for the
        # whole line costs seconds on a long history.
        named = _named(off_line, own, merged, names)
        line |= _show(repo_path, named, timeout) if named else {}
    except (OSError, subprocess.SubprocessError) as exc:
        # One line: a git timeout here is routine on a large history, not a crash.
        logger.debug("decision_landing_unreadable", error=f"{type(exc).__name__}: {exc}")
        return {}, frozenset()
    out: dict[str, Landing] = {}
    for stored, full in expanded.items():
        landing = _landing(full, own[full], line, names, merged)
        if landing is not None:
            out[stored] = landing
    return out, frozenset(expanded)


def _named(
    off_line: list[str], own: dict[str, _Commit], merged: dict[str, str], names: dict[str, set[str]]
) -> list[str]:
    """The line commits whose name matches a commit no merge accounts for."""
    unplaced = (full for full in off_line if full not in merged)
    return sorted({sha for full in unplaced for sha in names.get(own[full].subject.strip(), ())})


def is_commit_ref(value: str) -> bool:
    """Whether *value* reads as a commit sha, the evidence a landing can be found for."""
    return bool(_SHA.fullmatch(value.strip().lower()))


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
    full: str,
    own: _Commit,
    line: dict[str, _Commit],
    names: dict[str, set[str]],
    merged: dict[str, str],
) -> Landing | None:
    if full in line:
        return Landing(full, line[full].at)
    if full in merged:
        return Landing(merged[full], line[merged[full]].at)
    later = [
        s
        for s in names.get(own.subject.strip(), ())
        # Every file the commit changed, not one: a same-named commit sharing a
        # file is not the squash or rebase that carried it.
        if line[s].at >= own.at and own.files and own.files <= line[s].files
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
    return _show(repo_path, full, timeout) if full else {}


def _show(repo_path: Path | str, shas: list[str], timeout: float | None) -> dict[str, _Commit]:
    """*shas* with the files each changed."""
    shown = run_git(
        ["log", "--no-walk=unsorted", "--name-only", _FORMAT, *shas], repo_path, **_bound(timeout)
    )
    return _parse(shown.stdout) if shown.returncode == 0 else {}


def _first_parent(
    repo_path: Path | str, since: datetime, ref: str, timeout: float | None
) -> dict[str, _Commit]:
    """*ref*'s first-parent line since *since*, newest first, without files."""
    shown = run_git(
        ["log", "--first-parent", f"--since={since.isoformat()}", _FORMAT, ref],
        repo_path,
        **_bound(timeout),
    )
    return _parse(shown.stdout) if shown.returncode == 0 else {}


def _merged_in(
    repo_path: Path | str,
    since: datetime,
    ref: str,
    line: dict[str, _Commit],
    timeout: float | None,
) -> dict[str, str]:
    """Each commit a first-parent merge brought in -> that merge, the oldest one."""
    shown = run_git(
        ["log", "--format=%H %P", f"--since={since.isoformat()}", ref],
        repo_path,
        **_bound(timeout),
    )
    if shown.returncode != 0:
        return {}
    parents = {row[0]: row[1:] for row in (r.split() for r in shown.stdout.splitlines()) if row}
    out: dict[str, str] = {}
    # ``line`` is newest first; walking it oldest first credits a commit two
    # merges reach to the one that brought it in.
    for merge in reversed(list(line)):
        stack = list(parents.get(merge, ())[1:])
        while stack:
            sha = stack.pop()
            if sha in line or sha in out or sha not in parents:
                continue
            out[sha] = merge
            stack.extend(parents[sha])
    return out


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
