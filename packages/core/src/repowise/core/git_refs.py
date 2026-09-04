"""Read-only git ref queries: branches, revisions, diffs, ahead/behind.

One helper because the pieces were scattered or missing: listing branch tips
with their dates existed nowhere, and ahead/behind existed only one way. Query
time only: it reads a live repository, never writes, never walks the tree.
Every call degrades to an empty value instead of raising.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from .analysis.change_risk.features import _git

__all__ = [
    "BranchRef",
    "ahead_behind",
    "ahead_behind_many",
    "changed_files",
    "commit_file_sets",
    "current_branch",
    "default_base",
    "files_by_ref",
    "list_branches",
    "refs_containing",
    "refs_merged_into",
    "resolve",
    "toplevel",
]

#: Subprocess failures that mean "no answer", not "bug": a wedged or missing
#: git, a path that is not a repository, a bad revspec.
_GIT_FAILURES = (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError)

#: Candidate trunks, in the order a repository without ``origin/HEAD`` names one.
_FALLBACK_BASES = ("main", "master")


def _read(repo_path: str, args: list[str]) -> str:
    """One git call, stripped stdout, ``""`` when git cannot answer."""
    try:
        return _git(args, cwd=repo_path, check=False).strip()
    except _GIT_FAILURES:
        return ""


@dataclass(frozen=True)
class BranchRef:
    """One branch tip: the short ref, its commit, and when that commit landed."""

    name: str  # short ref: "feat/x" or "origin/feat/x"
    sha: str
    committed_at: str  # committerdate:iso-strict, "" if unknown
    is_remote: bool
    # The same instant as an epoch, so callers order without parsing a date.
    committed_unix: int = 0
    ref: str = ""  # full refname, which is what for-each-ref takes as a pattern


def toplevel(repo_path: str) -> str:
    """The repository root holding *repo_path*, ``""`` when it is not a repo.

    Paths in a diff are relative to this, so a caller given a subdirectory has
    to resolve it before it can match a path against an index.
    """
    return _read(repo_path, ["rev-parse", "--show-toplevel"])


def current_branch(repo_path: str) -> str | None:
    """The checked-out branch, or ``None`` when detached or unreadable."""
    name = _read(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    # A detached HEAD reports the literal "HEAD", which names no branch.
    return name if name and name != "HEAD" else None


def resolve(repo_path: str, rev: str) -> str:
    """The commit *rev* names, ``""`` when it names none."""
    return _read(repo_path, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])


def default_base(repo_path: str) -> str:
    """The branch a change is most likely cut from, else ``HEAD``."""
    head = _read(repo_path, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if head:
        return head
    for name in _FALLBACK_BASES:
        if resolve(repo_path, name):
            return name
    return "HEAD"


def _parse_ref_line(line: str) -> BranchRef | None:
    """One ``for-each-ref`` row, or ``None`` when it names no usable branch."""
    parts = line.split("\t")
    if len(parts) != 5:
        return None
    short, sha, committed_at, unix, refname = parts
    # refs/remotes/origin/HEAD aliases another ref already in this list.
    if not short or not sha or refname.endswith("/HEAD"):
        return None
    return BranchRef(
        name=short,
        sha=sha,
        committed_at=committed_at,
        is_remote=refname.startswith("refs/remotes/"),
        committed_unix=int(unix) if unix.strip().isdigit() else 0,
        ref=refname,
    )


def list_branches(repo_path: str) -> list[BranchRef]:
    """Local and remote-tracking branch tips, newest committer date first."""
    out = _read(
        repo_path,
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)%09%(objectname)%09%(committerdate:iso-strict)%09%(committerdate:unix)%09%(refname)",
            "refs/heads",
            "refs/remotes",
        ],
    )
    refs: list[BranchRef] = []
    for line in out.splitlines():
        ref = _parse_ref_line(line)
        if ref is None:
            continue
        refs.append(ref)
    return refs


def ahead_behind_many(
    repo_path: str, base: str, refs: Sequence[str]
) -> dict[str, tuple[int, int]]:
    """``(ahead, behind)`` against *base* for each full refname, in one call.

    ``%(ahead-behind:...)`` needs git 2.36, so an older git yields ``{}`` and
    the caller falls back to asking per ref.
    """
    if not refs:
        return {}
    out = _read(
        repo_path,
        ["for-each-ref", f"--format=%(refname:short)%09%(ahead-behind:{base})", *refs],
    )
    counts: dict[str, tuple[int, int]] = {}
    for line in out.splitlines():
        name, _, pair = line.partition("\t")
        parts = pair.split()
        if not name or len(parts) != 2 or not all(p.isdigit() for p in parts):
            continue
        counts[name] = (int(parts[0]), int(parts[1]))
    return counts


def _reachable_refs(repo_path: str, flag: str, rev: str) -> set[str]:
    """Short names of the branches ``for-each-ref <flag> <rev>`` selects."""
    out = _read(
        repo_path,
        ["for-each-ref", flag, rev, "--format=%(refname:short)", "refs/heads", "refs/remotes"],
    )
    return {line.strip() for line in out.splitlines() if line.strip()}


def refs_containing(repo_path: str, rev: str) -> set[str]:
    """Branches whose history already holds *rev*: work stacked on top of it."""
    return _reachable_refs(repo_path, "--contains", rev)


def refs_merged_into(repo_path: str, rev: str) -> set[str]:
    """Branches *rev* already holds: what it was stacked on, and what merged in."""
    return _reachable_refs(repo_path, "--merged", rev)


def changed_files(repo_path: str, base: str, head: str) -> list[str]:
    """Paths ``head`` changed since it forked from ``base``, sorted and deduped."""
    # Three dots: what head did, not what base did in the meantime.
    out = _read(repo_path, ["diff", "--name-only", f"{base}...{head}"])
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def _short_ref(name: str) -> str:
    """``%S`` echoes the command-line argument, but a full refname can arrive."""
    for prefix in ("refs/heads/", "refs/remotes/"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def files_by_ref(repo_path: str, base: str, refs: Sequence[str]) -> dict[str, frozenset[str]]:
    """Paths each ref changed since *base*, from one walk over all of them.

    ``%S`` credits a commit to the ref that reached it first, so two refs
    sharing a commit see it once. A ref with no commits of its own is absent.
    ``%S`` needs git 2.21; an older git yields ``{}``.
    """
    if not refs:
        return {}
    out = _read(
        repo_path,
        ["log", "--source", "--format=%x00%S", "--name-only", *refs, f"^{base}"],
    )
    by_ref: dict[str, set[str]] = {}
    for block in out.split("\x00")[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        paths = by_ref.setdefault(_short_ref(lines[0].strip()), set())
        paths.update(line.strip() for line in lines[1:] if line.strip())
    return {name: frozenset(paths) for name, paths in by_ref.items()}


def commit_file_sets(repo_path: str, revspec: str | None) -> list[frozenset[str]]:
    """The files each commit of a range touched, oldest first.

    Only a range carries the information: a single revision names one commit,
    and one commit says nothing about what moves with what.
    """
    if not revspec:
        return []
    # The commits of "a...b" are "a..b"; three dots is diff syntax, not a range.
    if "..." in revspec:
        revspec = revspec.replace("...", "..")
    elif ".." not in revspec:
        return []
    out = _read(repo_path, ["log", "--reverse", "--format=%x00%H", "--name-only", revspec])
    sets: list[frozenset[str]] = []
    for block in out.split("\x00"):
        paths = {line.strip() for line in block.splitlines()[1:] if line.strip()}
        if paths:
            sets.append(frozenset(paths))
    return sets


def ahead_behind(repo_path: str, base: str, head: str) -> tuple[int, int]:
    """``(ahead, behind)``: commits only on ``head``, then only on ``base``."""
    parts = _read(repo_path, ["rev-list", "--left-right", "--count", f"{base}...{head}"]).split()
    if len(parts) != 2:
        return (0, 0)
    try:
        # git prints left (only in base) first, the caller wants head first.
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return (0, 0)
    return (ahead, behind)
