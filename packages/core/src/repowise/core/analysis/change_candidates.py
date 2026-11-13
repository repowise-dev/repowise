"""The changes a reviewer can pick to see a blast radius.

Three sources, in the order a reader most likely wants them: uncommitted work
in the checkout, branches ahead of the default branch, and recent indexed
commits. The first two need a local checkout; the commits come from the index,
so a server without a checkout still offers them. Every candidate resolves to
a list of changed paths through :func:`resolve_change_files`, which only
accepts a branch or commit this module listed, or a merge or pull request read
from the ref its forge publishes, so a client never hands git an arbitrary
revision.
"""

from __future__ import annotations

import asyncio
import logging
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
from repowise.core.forges import BaseForge, ForgeKind, all_forges, detect_forge, get_forge
from repowise.core.persistence.models import GitCommit, GitCommitFile

log = logging.getLogger(__name__)

#: A ``change_request`` (a merge or pull request) is never listed: it opens
#: from a link, through :func:`resolve_change_request`.
ChangeKind = Literal["working_tree", "branch", "commit", "change_request"]

WORKING_TREE_ID = "working-tree"
#: Listed commits carry the full sha; a prefix could name two commits at once.
_SHA = re.compile(r"^[0-9a-f]{40}$")
#: A merge or pull request by number, ``mr:<n>`` or ``pr:<n>`` on any forge, or
#: pinned to the two commits it was read at, ``mr:<n>@<base sha>...<head sha>``,
#: so a shared link keeps naming the same diff after the request moves on.
_CHANGE_REQUEST = re.compile(
    r"^(mr|pr):([1-9][0-9]{0,8})(?:@([0-9a-f]{40})\.\.\.([0-9a-f]{40}))?$"
)


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
    #: For a change request: its number on the forge.
    number: int | None = None
    #: For a change request: the commit it is compared against.
    base_sha: str | None = None
    #: For a change request: what its forge calls one ("merge request", "pull request").
    change_term: str | None = None

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
#: on another worktree's branch. Past that lifetime the kept listing is still
#: served while one background read replaces it, so only a refs move makes a
#: reader wait for git.
_BRANCH_TTL_SECONDS = 30.0
_BRANCHES: dict[str, tuple[tuple[Any, ...], float, list[ChangeCandidate]]] = {}
#: The background read running per checkout, at most one each.
_REFRESHING: dict[str, threading.Thread] = {}
_REFRESH_LOCK = threading.Lock()


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
    """:func:`_list_branches`, reused while the refs state holds.

    An entry past its lifetime is served as it is while a background read
    replaces it; a refs move is read before answering.
    """
    state = (_refs_state(repo_path), base, current, max_branches)
    hit = _BRANCHES.get(repo_path)
    if hit and hit[0] == state:
        if time.monotonic() - hit[1] >= _BRANCH_TTL_SECONDS:
            _refresh_branches(repo_path, state)
        return hit[2]
    branches = _list_branches(repo_path, base, current, max_branches)
    _BRANCHES[repo_path] = (state, time.monotonic(), branches)
    return branches


def _refresh_branches(repo_path: str, state: tuple[Any, ...]) -> None:
    """Re-read *repo_path*'s branches on a daemon thread, unless a read is already running."""
    _, base, current, max_branches = state

    def read() -> None:
        try:
            branches = _list_branches(repo_path, base, current, max_branches)
            _BRANCHES[repo_path] = (state, time.monotonic(), branches)
        except Exception:  # git failed: the kept listing stays, and the next call tries again
            log.debug("branch listing refresh failed", exc_info=True)
        finally:
            with _REFRESH_LOCK:
                _REFRESHING.pop(repo_path, None)

    with _REFRESH_LOCK:
        if repo_path in _REFRESHING:
            return
        thread = _REFRESHING[repo_path] = threading.Thread(
            target=read, name="repowise-branch-refresh", daemon=True
        )
    thread.start()


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


def is_change_request(change_id: str) -> bool:
    """Whether *change_id* names a merge or pull request, pinned or not."""
    return bool(_CHANGE_REQUEST.fullmatch(change_id))


def _request_forge(repo_path: str, prefix: str) -> BaseForge:
    """The checkout's forge; for a host no setting maps (a self-managed GitLab
    on its own domain reads as plain git), the forge the id's prefix names."""
    own = get_forge(detect_forge(repo_path))
    if own.kind != ForgeKind.GENERIC:
        return own
    return get_forge(ForgeKind.GITLAB if prefix == "mr" else ForgeKind.GITHUB)


def _published_head(repo_path: str, prefix: str, number: int) -> tuple[BaseForge, str]:
    """The forge whose published ref holds request *number*, and the head it points at.

    :func:`_request_forge` is asked first, then every other forge's ref
    shape. ``(that forge, "")`` when no ref is here.
    """
    first = _request_forge(repo_path, prefix)
    for forge in (first, *(f for f in all_forges() if f is not first)):
        if forge.change_head_ref:
            sha = git_refs.ref_sha(repo_path, forge.change_head_ref.format(number=number))
            if sha:
                return forge, sha
    return first, ""


def change_request_ref(repo_path: str, change_id: str) -> str:
    """The ref the request's head is published under, to fetch; ``""`` when there is none."""
    match = _CHANGE_REQUEST.fullmatch(change_id)
    if not match:
        return ""
    template = _request_forge(repo_path, match.group(1)).change_head_ref
    return template.format(number=int(match.group(2))) if template else ""


def _request_commits(
    repo_path: str, match: re.Match[str], name: str, published: str, base: str
) -> tuple[str, str]:
    """``(base sha, head sha)``: a pinned id's own pair, else the published head and its fork point."""
    if match.group(4):
        base_sha, head_sha = match.group(3), match.group(4)
        if not (
            git_refs.commit_exists(repo_path, base_sha)
            and git_refs.commit_exists(repo_path, head_sha)
        ):
            raise UnknownChangeError(f"the commits of {name} are not in this clone")
        return base_sha, head_sha
    if not published:
        raise UnknownChangeError(f"{name} is not in this clone; fetch its ref first")
    # Ceiling: the default branch stands in for the request's target branch,
    # which only the forge knows; a request into another branch reads wider.
    base_sha = git_refs.change_base(repo_path, f"{base}...{published}")
    if not base_sha:
        raise UnknownChangeError(f"{name} shares no history with {base}")
    return base_sha, published


def resolve_change_request(repo_path: str, change_id: str) -> ChangeCandidate:
    """The merge or pull request *change_id* names, pinned to its base and head commits.

    Read from this clone only. The head is the ref the forge publishes
    (``refs/merge-requests/<n>/head`` on GitLab, ``refs/pull/<n>/head`` on
    GitHub) and the base its merge base with the default branch; a pinned id
    reads its two commits as given. The returned ``id`` is always the pinned
    form. Raises :class:`UnknownChangeError` when the ref or the commits are
    not in this clone.
    """
    match = _CHANGE_REQUEST.fullmatch(change_id)
    if not match:
        raise UnknownChangeError(f"unknown change {change_id!r}")
    prefix, number = match.group(1), int(match.group(2))
    forge, published = _published_head(repo_path, prefix, number)
    name = f"{forge.change_term} {forge.format_change_ref(number)}"
    base = git_refs.default_base(repo_path)
    base_sha, head_sha = _request_commits(repo_path, match, name, published, base)
    # The subject last: it is the one field that can hold a tab.
    tip = _git(["log", "-1", "--format=%an%x09%cI%x09%s", head_sha], cwd=repo_path, check=False)
    author, when, subject = [*tip.strip().split("\t", 2), "", ""][:3]
    return ChangeCandidate(
        id=f"{prefix}:{number}@{base_sha}...{head_sha}",
        kind="change_request",
        label=forge.format_change_ref(number),
        title=subject or None,
        author=author or None,
        committed_at=when or None,
        head_sha=head_sha,
        base=base,
        files_changed=len(_diff_files(repo_path, base_sha, base_sha, head_sha, head_sha)),
        number=number,
        base_sha=base_sha,
        change_term=forge.change_term,
    )


def _change_request_files(repo_path: str, change_id: str) -> list[str]:
    change = resolve_change_request(repo_path, change_id)
    base_sha, head_sha = change.base_sha or "", change.head_sha or ""
    return _relative(repo_path, _diff_files(repo_path, base_sha, base_sha, head_sha, head_sha))


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

    if is_change_request(change_id):
        if not repo_path:
            raise UnknownChangeError("this server has no checkout to read the change from")
        return await asyncio.to_thread(_change_request_files, repo_path, change_id)

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
    change request ``<base sha>...<head sha>``, a commit its sha. Validated like :func:`resolve_change_files`, so no client
    string reaches git as an option or a range.
    """
    if change_id == WORKING_TREE_ID:
        return None
    kind, _, ref = change_id.partition(":")
    if kind == "branch":
        # One exact ref lookup: listing every branch to find this one takes
        # seconds on a repository with thousands of them.
        if not _branch_tip(repo_path, ref):
            raise UnknownChangeError(f"unknown branch {ref!r}")
        return f"{git_refs.default_base(repo_path)}...{ref}"
    if is_change_request(change_id):
        change = resolve_change_request(repo_path, change_id)
        return f"{change.base_sha}...{change.head_sha}"
    if kind == "commit" and _SHA.match(ref) and git_refs.resolve(repo_path, ref):
        return ref
    raise UnknownChangeError(f"unknown change {change_id!r}")


def change_identity(repo_path: str, change_id: str) -> tuple[str, ...] | None:
    """The commits a listed change is read from, so an answer about it can be
    kept until one of them moves; ``None`` for uncommitted work, which moves
    without a commit.

    A branch is its tip and the default branch's tip, the two ends of what it
    would bring; a commit, or a merge request pinned to its commits, is its
    id. The checkout's ``HEAD`` joins each, since a score ranks the change against the history behind it.
    """
    if change_id == WORKING_TREE_ID:
        return None
    head = git_refs.resolve(repo_path, "HEAD")
    kind, _, ref = change_id.partition(":")
    if kind == "branch":
        tip = _branch_tip(repo_path, ref)
        if not tip:
            raise UnknownChangeError(f"unknown branch {ref!r}")
        base = git_refs.resolve(repo_path, git_refs.default_base(repo_path))
        return (change_id, tip, base, head)
    if kind == "commit" and _SHA.match(ref):
        return (change_id, head)
    match = _CHANGE_REQUEST.fullmatch(change_id)
    if match and match.group(4):  # a pinned request names its own two commits
        return (change_id, head)
    raise UnknownChangeError(f"unknown change {change_id!r}")
