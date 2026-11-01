"""The changes a reviewer can pick to see a blast radius.

Three sources, in the order a reader most likely wants them: uncommitted work
in the checkout, branches ahead of the default branch, and recent indexed
commits. The first two need a local checkout; the commits come from the index,
so a server without a checkout still offers them. Every candidate resolves to
a list of changed paths through :func:`resolve_change_files`, which only
accepts a branch or commit this module listed, so a client never hands git an
arbitrary revision.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core import git_refs
from repowise.core.analysis.git_cli import _git, checkout_relative
from repowise.core.persistence.models import GitCommit, GitCommitFile

#: ``pr`` is never listed here (a local checkout knows no pull requests); a
#: host that does lists them with ``number``, ``base_sha`` and ``head_sha``.
ChangeKind = Literal["working_tree", "branch", "commit", "pull_request"]

WORKING_TREE_ID = "working-tree"
#: Listed commits carry the full sha; a prefix could name two commits at once.
_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ChangeCandidate:
    """One pickable change. ``id`` is what a client sends back to analyze it."""

    id: str
    kind: ChangeKind
    label: str
    title: str | None = None
    author: str | None = None
    committed_at: str | None = None
    head_sha: str | None = None
    #: The branch it is compared against, for a branch.
    base: str | None = None
    #: Commits only on this branch, for a branch.
    ahead: int | None = None
    #: Paths it touches; ``None`` when not counted.
    files_changed: int | None = None
    #: The checked-out branch.
    is_current: bool = False
    #: For a branch: whether it exists on the remote (a local-only branch is
    #: often scratch work).
    pushed: bool | None = None
    #: For a pull request: its number.
    number: int | None = None
    #: For a pull request: the base commit it is compared against.
    base_sha: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChangeCandidates:
    """What :func:`list_change_candidates` found, and which one to open first."""

    changes: list[ChangeCandidate]
    default_id: str | None
    #: Whether this server can read the checkout (uncommitted work, branches).
    working_tree_supported: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "changes": [c.to_dict() for c in self.changes],
            "default_id": self.default_id,
            "working_tree_supported": self.working_tree_supported,
        }


def _relative(repo_path: str, paths: list[str]) -> list[str]:
    """git's top-level paths, relative to the checkout the index was built in."""
    try:
        return sorted(checkout_relative(repo_path, dict.fromkeys(paths)))
    except Exception:
        return sorted(paths)


def _working_tree_paths(repo_path: str) -> list[str]:
    """Paths with uncommitted changes, untracked files included, as the index names them."""
    try:
        out = _git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=repo_path)
    except Exception:
        return []
    paths: list[str] = []
    fields = out.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in "RC":
            # A rename or copy names its source in the next field.
            i += 1
        paths.append(path)
    return _relative(repo_path, sorted(set(paths)))


def _branch_subjects(repo_path: str, refs: list[str]) -> dict[str, tuple[str, str]]:
    """``{short name: (tip subject, tip author)}`` for full refnames, in one call."""
    if not refs:
        return {}
    try:
        out = _git(
            [
                "for-each-ref",
                "--format=%(refname:short)%09%(contents:subject)%09%(authorname)",
                *refs,
            ],
            cwd=repo_path,
            check=False,
        )
    except Exception:
        return {}
    subjects: dict[str, tuple[str, str]] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            subjects[parts[0]] = (parts[1], parts[2])
    return subjects


def _wip_candidate(repo_path: str, current: str | None) -> ChangeCandidate | None:
    """Uncommitted work in the checkout, or ``None`` when it is clean."""
    paths = _working_tree_paths(repo_path)
    if not paths:
        return None
    return ChangeCandidate(
        id=WORKING_TREE_ID,
        kind="working_tree",
        label="Uncommitted changes",
        title=f"On {current}" if current else None,
        files_changed=len(paths),
        is_current=True,
    )


#: Refs asked for their ahead count per ``for-each-ref`` call. Every ref is an
#: argument, and Windows caps a command line near 32K characters, so asking for
#: a repository's hundreds of refs at once failed and fell back to one
#: ``rev-list`` per branch.
_AHEAD_CHUNK = 40


def _ahead_counts(
    repo_path: str, base: str, refs: list[git_refs.BranchRef], want: int
) -> list[tuple[git_refs.BranchRef, int]]:
    """The first *want* of *refs* (newest first) with commits *base* lacks, with the count."""
    kept: list[tuple[git_refs.BranchRef, int]] = []
    for start in range(0, len(refs), _AHEAD_CHUNK):
        chunk = refs[start : start + _AHEAD_CHUNK]
        counts = git_refs.ahead_behind_many(repo_path, base, [r.ref for r in chunk])
        for ref in chunk:
            pair = counts.get(ref.name) or git_refs.ahead_behind(repo_path, base, ref.name)
            if pair[0] > 0:
                kept.append((ref, pair[0]))
            if len(kept) >= want:
                return kept
    return kept


def _branches_ahead(
    repo_path: str, base: str, branches: list[git_refs.BranchRef], max_branches: int
) -> list[tuple[git_refs.BranchRef, int]]:
    """Branches with commits *base* lacks, newest first, each with its ahead count."""
    base_sha = git_refs.resolve(repo_path, base)
    trunk = base.removeprefix("origin/")
    local_tips = {r.name: r.sha for r in branches if not r.is_remote}
    refs = [
        r
        for r in branches
        if r.sha != base_sha
        and r.name not in (base, trunk, f"origin/{trunk}")
        # origin/x at the same tip as a local x is the same work twice.
        and not (r.is_remote and local_tips.get(r.name.split("/", 1)[-1]) == r.sha)
    ]
    return _ahead_counts(repo_path, base, refs, max_branches)


#: A branch's files keyed by the two commits they are read between, so a
#: branch whose tip and base have not moved is never diffed twice. Keyed by
#: content, so it never goes stale; bounded, so it never grows without end.
_DIFFS: OrderedDict[tuple[str, str, str], list[str]] = OrderedDict()
_DIFFS_SIZE = 256
_DIFFS_LOCK = threading.Lock()


def _diff_files(repo_path: str, base: str, base_sha: str, head: str, head_sha: str) -> list[str]:
    """:func:`git_refs.changed_files`, kept per (checkout, base commit, head commit)."""
    key = (repo_path, base_sha, head_sha)
    keep = bool(base_sha and head_sha)
    with _DIFFS_LOCK:
        if keep and key in _DIFFS:
            _DIFFS.move_to_end(key)
            return _DIFFS[key]
    # Diff the commit the key names, so the cache never holds another ref's answer
    # (a tag can share a branch's name).
    files = git_refs.changed_files(repo_path, base, head_sha or head)
    if keep:
        with _DIFFS_LOCK:
            _DIFFS[key] = files
            while len(_DIFFS) > _DIFFS_SIZE:
                _DIFFS.popitem(last=False)
    return files


def _branch_diffs(
    repo_path: str, base: str, kept: list[tuple[git_refs.BranchRef, int]]
) -> dict[str, list[str]]:
    """Each kept branch's files; the diffs run side by side, each its own git process."""
    base_sha = git_refs.resolve(repo_path, base)
    refs = [r for r, _ in kept]
    with ThreadPoolExecutor(max_workers=6) as pool:
        files = list(
            pool.map(lambda r: _diff_files(repo_path, base, base_sha, r.name, r.sha), refs)
        )
    return {r.name: f for r, f in zip(refs, files, strict=True)}


#: Listed branches per checkout, reused for a short while: reading a large
#: repository's refs and diffing each branch is most of a listing's time, and
#: a reader flips between changes far more often than branches move. Keyed by
#: what moves when a branch does (HEAD, packed refs, the last fetch), with a
#: short lifetime for the moves that leave those alone, such as a commit made
#: on another worktree's branch.
_BRANCH_TTL_SECONDS = 30.0
_BRANCHES: dict[str, tuple[tuple[Any, ...], float, list[ChangeCandidate]]] = {}


def _refs_state(repo_path: str) -> tuple[Any, ...]:
    """HEAD's commit and the mtimes of the files a fetch or a ref pack rewrites."""
    common = git_refs.common_dir(repo_path)

    def mtime(name: str) -> float:
        try:
            return (Path(common) / name).stat().st_mtime if common else 0.0
        except OSError:
            return 0.0

    return (git_refs.resolve(repo_path, "HEAD"), mtime("packed-refs"), mtime("FETCH_HEAD"))


def _cached_branches(
    repo_path: str, base: str, current: str | None, max_branches: int
) -> list[ChangeCandidate]:
    """:func:`_list_branches`, reused while the refs state holds and the entry is young."""
    state = (_refs_state(repo_path), base, current, max_branches)
    hit = _BRANCHES.get(repo_path)
    if hit and hit[0] == state and time.monotonic() - hit[1] < _BRANCH_TTL_SECONDS:
        return hit[2]
    branches = _list_branches(repo_path, base, current, max_branches)
    _BRANCHES[repo_path] = (state, time.monotonic(), branches)
    return branches


def clear_branch_cache() -> None:
    """Forget every kept listing and diff, for a test that moves refs under one checkout."""
    _BRANCHES.clear()
    with _DIFFS_LOCK:
        _DIFFS.clear()


def _list_branches(
    repo_path: str, base: str, current: str | None, max_branches: int
) -> list[ChangeCandidate]:
    """The branches ahead of *base*, as candidates, from one read of the refs."""
    all_refs = git_refs.list_branches(repo_path)
    kept = _branches_ahead(repo_path, base, all_refs, max_branches)
    remote_names = {r.name.split("/", 1)[-1] for r in all_refs if r.is_remote}
    # The same three-dot diff the analysis reads, so the count in the list is
    # the count the verdict reports.
    files = _branch_diffs(repo_path, base, kept)
    subjects = _branch_subjects(repo_path, [r.ref for r, _ in kept])
    return [
        ChangeCandidate(
            id=f"branch:{ref.name}",
            kind="branch",
            label=ref.name,
            title=subjects.get(ref.name, ("", ""))[0] or None,
            author=subjects.get(ref.name, ("", ""))[1] or None,
            committed_at=ref.committed_at or None,
            head_sha=ref.sha,
            base=base,
            ahead=ahead,
            files_changed=len(files[ref.name]),
            is_current=ref.name == current,
            pushed=ref.is_remote or ref.name in remote_names,
        )
        for ref, ahead in kept
    ]


def _git_candidates(
    repo_path: str, max_branches: int
) -> tuple[ChangeCandidate | None, list[ChangeCandidate]]:
    """Uncommitted work (read fresh every time) and the branches ahead of the default branch."""
    current = git_refs.current_branch(repo_path)
    wip = _wip_candidate(repo_path, current)
    base = git_refs.default_base(repo_path)
    if base == "HEAD":
        return wip, []
    return wip, _cached_branches(repo_path, base, current, max_branches)


async def _commit_candidates(
    session: AsyncSession, repo_id: str, limit: int
) -> list[ChangeCandidate]:
    """The newest indexed commits; the index holds their files, so no checkout is needed."""
    rows = (
        await session.execute(
            select(GitCommit)
            .where(GitCommit.repository_id == repo_id)
            .order_by(GitCommit.committed_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [
        ChangeCandidate(
            id=f"commit:{c.sha}",
            kind="commit",
            label=c.sha[:8],
            title=c.subject or None,
            author=c.author_name or None,
            committed_at=c.committed_at.isoformat() if c.committed_at else None,
            head_sha=c.sha,
            files_changed=c.files_changed,
        )
        for c in rows
    ]


async def list_change_candidates(
    session: AsyncSession,
    repo_id: str,
    repo_path: str | None,
    *,
    max_branches: int = 12,
    max_commits: int = 15,
) -> ChangeCandidates:
    """Every change a reviewer can pick, with the one to open first.

    *repo_path* ``None`` (no checkout on this server) offers commits only. The
    default is the uncommitted work when there is any, else the checked-out
    branch when it is ahead of the default branch, else the newest branch,
    else the newest commit.
    """
    # git runs off the event loop: a branch walk on a large repository is not instant.
    wip, branches = (
        await asyncio.to_thread(_git_candidates, repo_path, max_branches)
        if repo_path
        else (None, [])
    )
    commits = await _commit_candidates(session, repo_id, max_commits)
    changes = [*([wip] if wip else []), *branches, *commits]
    current = next((b for b in branches if b.is_current), None)
    first = (
        wip or current or (branches[0] if branches else None) or (commits[0] if commits else None)
    )
    return ChangeCandidates(
        changes=changes,
        default_id=first.id if first else None,
        working_tree_supported=repo_path is not None,
    )


class UnknownChangeError(ValueError):
    """The change id names nothing this repository offers."""


def _branch_files(repo_path: str, name: str) -> list[str]:
    """What a branch changed since it forked from the default branch."""
    # Only an exact branch ref, so the name is never parsed as an option or a range.
    head_sha = _branch_tip(repo_path, name)
    if not head_sha:
        raise UnknownChangeError(f"unknown branch {name!r}")
    base = git_refs.default_base(repo_path)
    files = _diff_files(repo_path, base, git_refs.resolve(repo_path, base), name, head_sha)
    return _relative(repo_path, files)


def _branch_tip(repo_path: str, name: str) -> str:
    """The commit a local or remote-tracking branch *name* points at, ``""`` for none."""
    for prefix in ("refs/heads/", "refs/remotes/"):
        sha = git_refs.ref_sha(repo_path, prefix + name)
        if sha:
            return sha
    return ""


async def resolve_change_files(
    session: AsyncSession,
    repo_id: str,
    repo_path: str | None,
    change_id: str,
) -> list[str]:
    """The repo-relative paths a listed change touches, deletions included.

    A deleted file still has dependents, and they are the ones a removal
    breaks, so it stays in the set. Raises :class:`UnknownChangeError` for an
    id this repository does not offer, including a branch or working-tree id
    on a server without a checkout.
    """
    if change_id == WORKING_TREE_ID:
        if not repo_path:
            raise UnknownChangeError("this server has no checkout to read uncommitted changes from")
        return await asyncio.to_thread(_working_tree_paths, repo_path)

    kind, _, ref = change_id.partition(":")
    if kind == "branch":
        if not repo_path:
            raise UnknownChangeError("this server has no checkout to read branches from")
        return await asyncio.to_thread(_branch_files, repo_path, ref)

    if kind == "commit" and _SHA.match(ref):
        rows = (
            await session.execute(
                select(GitCommitFile.file_path).where(
                    GitCommitFile.repository_id == repo_id,
                    GitCommitFile.sha == ref,
                )
            )
        ).scalars()
        paths = sorted(set(rows))
        if paths:
            return paths
        if repo_path and await asyncio.to_thread(git_refs.resolve, repo_path, ref):
            files = await asyncio.to_thread(git_refs.changed_files, repo_path, f"{ref}^", ref)
            return await asyncio.to_thread(_relative, repo_path, files)
        raise UnknownChangeError(f"unknown commit {ref!r}")

    raise UnknownChangeError(f"unknown change {change_id!r}")


def change_revspec(repo_path: str, change_id: str) -> str | None:
    """The revspec a health check judges for a listed change; ``None`` is the working tree.

    A branch is ``<default branch>...<branch>`` (what it would bring), a
    commit its sha. Validated like :func:`resolve_change_files`, so no client
    string reaches git as an option or a range.
    """
    if change_id == WORKING_TREE_ID:
        return None
    kind, _, ref = change_id.partition(":")
    if kind == "branch":
        if ref not in {b.name for b in git_refs.list_branches(repo_path)}:
            raise UnknownChangeError(f"unknown branch {ref!r}")
        return f"{git_refs.default_base(repo_path)}...{ref}"
    if kind == "commit" and _SHA.match(ref) and git_refs.resolve(repo_path, ref):
        return ref
    raise UnknownChangeError(f"unknown change {change_id!r}")
