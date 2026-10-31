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
from dataclasses import asdict, dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core import git_refs
from repowise.core.analysis.git_cli import _git, checkout_relative
from repowise.core.persistence.models import GitCommit, GitCommitFile

ChangeKind = Literal["working_tree", "branch", "commit"]

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
            ["for-each-ref", "--format=%(refname:short)%09%(contents:subject)%09%(authorname)", *refs],
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


def _branches_ahead(
    repo_path: str, base: str, max_branches: int
) -> list[tuple[git_refs.BranchRef, int]]:
    """Branches with commits *base* lacks, newest first, each with its ahead count."""
    base_sha = git_refs.resolve(repo_path, base)
    trunk = base.removeprefix("origin/")
    refs = [
        r
        for r in git_refs.list_branches(repo_path)
        if r.sha != base_sha and r.name not in (base, trunk, f"origin/{trunk}")
    ]
    counts = git_refs.ahead_behind_many(repo_path, base, [r.ref for r in refs])
    local_tips = {r.name: r.sha for r in refs if not r.is_remote}
    kept: list[tuple[git_refs.BranchRef, int]] = []
    for ref in refs:
        # origin/x at the same tip as a local x is the same work twice.
        if ref.is_remote and local_tips.get(ref.name.split("/", 1)[-1]) == ref.sha:
            continue
        ahead = counts[ref.name][0] if ref.name in counts else git_refs.ahead_behind(repo_path, base, ref.name)[0]
        if ahead > 0:
            kept.append((ref, ahead))
        if len(kept) >= max_branches:
            break
    return kept


def _git_candidates(repo_path: str, max_branches: int) -> tuple[ChangeCandidate | None, list[ChangeCandidate]]:
    """Uncommitted work and the branches ahead of the default branch."""
    current = git_refs.current_branch(repo_path)
    wip = _wip_candidate(repo_path, current)
    base = git_refs.default_base(repo_path)
    if base == "HEAD":
        return wip, []
    kept = _branches_ahead(repo_path, base, max_branches)
    remote_names = {r.name.split("/", 1)[-1] for r in git_refs.list_branches(repo_path) if r.is_remote}
    # The same three-dot diff the analysis reads, so the count in the list is
    # the count the verdict reports. One diff per kept branch, capped above.
    files = {r.name: git_refs.changed_files(repo_path, base, r.name) for r, _ in kept}
    subjects = _branch_subjects(repo_path, [r.ref for r, _ in kept])
    branches = [
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
    return wip, branches


async def _commit_candidates(session: AsyncSession, repo_id: str, limit: int) -> list[ChangeCandidate]:
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
        await asyncio.to_thread(_git_candidates, repo_path, max_branches) if repo_path else (None, [])
    )
    commits = await _commit_candidates(session, repo_id, max_commits)
    changes = [*([wip] if wip else []), *branches, *commits]
    current = next((b for b in branches if b.is_current), None)
    first = wip or current or (branches[0] if branches else None) or (commits[0] if commits else None)
    return ChangeCandidates(
        changes=changes,
        default_id=first.id if first else None,
        working_tree_supported=repo_path is not None,
    )


class UnknownChangeError(ValueError):
    """The change id names nothing this repository offers."""


def _branch_files(repo_path: str, name: str) -> list[str]:
    """What a branch changed since it forked from the default branch."""
    # Only a branch git lists, so the name is never parsed as an option or a range.
    if name not in {b.name for b in git_refs.list_branches(repo_path)}:
        raise UnknownChangeError(f"unknown branch {name!r}")
    return _relative(repo_path, git_refs.changed_files(repo_path, git_refs.default_base(repo_path), name))


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
