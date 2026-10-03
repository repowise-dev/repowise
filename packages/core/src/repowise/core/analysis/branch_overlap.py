"""Other open branches that edit the files this change edits.

Git alone answers who touches the same file. With an index the shared files are
ordered by how central they are, and history adds the files a branch edits that
move with ours. Without an index the git answer stands unchanged.

One bulk read picks which branches earn an exact diff, and a commit two
branches share is credited to one of them, so a branch can be missed there
but never listed on a file it does not edit.
"""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core import git_refs
from repowise.core.co_change import CoChangePartner, parse_partners
from repowise.core.persistence.batches import chunked
from repowise.core.persistence.models import GitMetadata, GraphNode
from repowise.core.workspace.cross_repo import _is_noise_path

from .pr_blast import PRBlastRadiusAnalyzer

# Newest branches diffed. A repository with hundreds of stale refs would spend
# more on the scan than the answer is worth; what was left out is reported
# through ``scanned`` and ``total`` rather than dropped silently.
BRANCH_SCAN_LIMIT = 50

# History rows corroborate a direct hit, they do not compete with it: past a
# few, one file that pairs with everything drowns out the files truly shared.
CO_CHANGE_ROWS_PER_BRANCH = 3

# Every dependency bump edits the manifest, so sharing one says nothing about
# the work. Kept here rather than in the shared noise list, which is counted on.
_MANIFEST_NAMES = frozenset(
    {
        "go.mod",
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
        "Gemfile",
        "composer.json",
        "requirements.txt",
        "setup.py",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
    }
)

# Git serves concurrent reads, and the exact diffs are the only per-hit cost left.
_DIFF_WORKERS = 4

_SAME_FILE = "same file"


@dataclass(frozen=True)
class OverlapRow:
    """One file another branch edits, and the reason it is listed."""

    file: str
    basis: str
    partner: str | None = None


@dataclass(frozen=True)
class BranchOverlapEntry:
    """One other branch, and where it meets this change."""

    branch: str
    committed_at: str
    committed_unix: int
    ahead: int
    behind: int
    rows: tuple[OverlapRow, ...]


@dataclass(frozen=True)
class BranchOverlap:
    """Every branch that edits a file this change edits, and what was scanned."""

    base: str
    current: str
    branches: tuple[BranchOverlapEntry, ...]
    scanned: int
    total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "current": self.current,
            "branches": [
                {
                    "branch": entry.branch,
                    "ahead": entry.ahead,
                    "behind": entry.behind,
                    "last_commit": entry.committed_at[:10],
                    "files": [_row_dict(row) for row in entry.rows],
                }
                for entry in self.branches
            ],
            "scanned": self.scanned,
            "total": self.total,
            "truncated": self.scanned < self.total,
            "summary": self._summary(),
        }

    def _summary(self) -> str:
        if not self.branches:
            return (
                "No other open branch edits a file this change edits "
                f"({self.scanned} scanned of {self.total})."
            )
        return (
            f"{len(self.branches)} of {self.scanned} open branches "
            f"({self.total} exist) edit files this change also edits."
        )


@dataclass(frozen=True)
class BranchScan:
    """The git answer, with what the index step needs to go further.

    Held apart from :class:`BranchOverlap` so the git work finishes before a
    database session is opened, rather than under one.
    """

    overlap: BranchOverlap
    others: dict[str, frozenset[str]] = field(default_factory=dict)
    ours: frozenset[str] = frozenset()


def _row_dict(row: OverlapRow) -> dict[str, Any]:
    out: dict[str, Any] = {"file": row.file}
    if row.partner:
        out["partner"] = row.partner
    out["basis"] = row.basis
    return out


def _signal(paths: Iterable[str]) -> frozenset[str]:
    """Paths worth pairing: a lockfile or manifest two branches both touch means nothing."""
    return frozenset(
        path
        for path in paths
        if path
        and not _is_noise_path(path)
        and path.replace("\\", "/").rsplit("/", 1)[-1] not in _MANIFEST_NAMES
    )


def _prefer(candidate: git_refs.BranchRef, kept: git_refs.BranchRef) -> bool:
    """Whether *candidate* is the better name for a tip already seen."""
    if candidate.is_remote != kept.is_remote:
        return kept.is_remote
    return len(candidate.name) < len(kept.name)


def _same_line_of_work(repo_path: str, current: str) -> set[str]:
    """Branches stacked on this one or that this one is stacked on: one change
    split over two refs shares every file, which is not two changes racing."""
    return git_refs.refs_containing(repo_path, current) | git_refs.refs_merged_into(
        repo_path, current
    )


def _candidates(
    repo_path: str, skip_names: set[str], skip_shas: set[str]
) -> list[git_refs.BranchRef]:
    """Branches worth diffing, newest first, one entry per distinct tip."""
    by_sha: dict[str, git_refs.BranchRef] = {}
    for ref in git_refs.list_branches(repo_path):
        if ref.name in skip_names or ref.sha in skip_shas:
            continue
        kept = by_sha.get(ref.sha)
        # Replacing the value keeps the newest-first position of the first sighting.
        if kept is None or _prefer(ref, kept):
            by_sha[ref.sha] = ref
    return list(by_sha.values())


def _worth_diffing(preview: frozenset[str] | None, ours: frozenset[str]) -> bool:
    """Absent means no commits of its own; empty means merge-only, so look anyway."""
    if preview is None:
        return False
    return not preview or bool(preview & ours)


def _order(entries: list[BranchOverlapEntry]) -> tuple[BranchOverlapEntry, ...]:
    """Most shared files first, then the most recent branch, then by name."""
    ordered = sorted(entries, key=lambda entry: entry.branch)
    ordered.sort(key=lambda entry: entry.committed_unix, reverse=True)
    ordered.sort(key=lambda entry: len(entry.rows), reverse=True)
    return tuple(ordered)


def scan_branches(
    repo_path: str,
    changed_files: Iterable[str],
    *,
    base: str,
    current: str = "HEAD",
    limit: int = BRANCH_SCAN_LIMIT,
) -> BranchScan:
    """Branch overlap from git alone: shared files, alphabetical, no history rows."""
    ours = _signal(changed_files)
    if not ours:
        return BranchScan(BranchOverlap(base, current, (), 0, 0))

    resolved = current
    if current == "HEAD":
        resolved = git_refs.current_branch(repo_path) or current
    refs = _candidates(
        repo_path,
        {base, current, resolved} | _same_line_of_work(repo_path, current),
        {
            sha
            for sha in (git_refs.resolve(repo_path, base), git_refs.resolve(repo_path, current))
            if sha
        },
    )

    scanned = refs[:limit]
    touched = git_refs.files_by_ref(repo_path, base, [ref.name for ref in scanned])
    # No bulk answer at all means a git too old for %S: diff every branch instead.
    probe_each = not touched and bool(scanned)

    probes = [
        ref for ref in scanned if probe_each or _worth_diffing(touched.get(ref.name), ours)
    ]

    def diff(ref: git_refs.BranchRef) -> frozenset[str]:
        # Rows come from the exact three-dot diff, never from the bulk read: a file
        # changed and reverted on a branch is in the bulk read but not in the diff.
        return _signal(git_refs.changed_files(repo_path, base, ref.name))

    hit: list[tuple[git_refs.BranchRef, list[str]]] = []
    others: dict[str, frozenset[str]] = {}
    with ThreadPoolExecutor(max_workers=_DIFF_WORKERS) as pool:
        # map keeps the candidate order, so the answer does not depend on timing.
        for ref, theirs in zip(probes, pool.map(diff, probes), strict=True):
            shared = sorted(theirs & ours)
            # No shared file means no entry: history alone never puts a branch on the list.
            if not shared:
                continue
            hit.append((ref, shared))
            others[ref.name] = theirs

    counts = git_refs.ahead_behind_many(repo_path, base, [ref.ref for ref, _ in hit if ref.ref])
    entries: list[BranchOverlapEntry] = []
    for ref, shared in hit:
        # A git too old for the bulk count still answers one ref at a time.
        ahead, behind = counts.get(ref.name) or git_refs.ahead_behind(repo_path, base, ref.name)
        entries.append(
            BranchOverlapEntry(
                branch=ref.name,
                committed_at=ref.committed_at,
                committed_unix=ref.committed_unix,
                ahead=ahead,
                behind=behind,
                rows=tuple(OverlapRow(file=path, basis=_SAME_FILE) for path in shared),
            )
        )

    overlap = BranchOverlap(
        base=base,
        current=resolved,
        branches=_order(entries),
        scanned=len(scanned),
        total=len(refs),
    )
    return BranchScan(overlap=overlap, others=others, ours=ours)


async def _read_index(
    session: AsyncSession, repo_id: str, hits: list[str], ours: list[str]
) -> tuple[dict[str, float], dict[str, list[CoChangePartner]]]:
    """``(shared file -> hub score, our file -> co-change partners)``, two batched reads."""
    pagerank: dict[str, float] = {}
    for chunk in chunked(hits):
        result = await session.execute(
            select(GraphNode.node_id, GraphNode.pagerank).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_type == "file",
                GraphNode.node_id.in_(chunk),
            )
        )
        pagerank.update({node_id: float(rank or 0.0) for node_id, rank in result.all()})

    temporal: dict[str, float] = {}
    partners: dict[str, list[CoChangePartner]] = {}
    for chunk in chunked(ours):
        result = await session.execute(
            select(
                GitMetadata.file_path,
                GitMetadata.temporal_hotspot_score,
                GitMetadata.co_change_partners_json,
            ).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path.in_(chunk),
            )
        )
        for path, score, raw in result.all():
            temporal[path] = float(score or 0.0)
            partners[path] = parse_partners(raw)

    hub = {
        path: PRBlastRadiusAnalyzer._score_file(temporal.get(path, 0.0), pagerank.get(path, 0.0))
        for path in hits
    }
    return hub, partners


def _co_change_rows(
    partners: dict[str, list[CoChangePartner]], theirs: frozenset[str], hits: set[str]
) -> list[OverlapRow]:
    """Files the branch edits that history pairs with one of ours, strongest first."""
    best: dict[str, tuple[int, OverlapRow]] = {}
    for our_file in sorted(partners):
        for partner in partners[our_file]:
            path = partner.file_path
            if path in hits or path not in theirs:
                continue
            # Without both counts the row cannot state its own basis, so it is not made.
            if not partner.support or not partner.self_commits:
                continue
            # Below half, the basis on the wire would read as a pair that mostly does not hold.
            if partner.support * 2 < partner.self_commits:
                continue
            seen = best.get(path)
            if seen is None or partner.support > seen[0]:
                best[path] = (
                    partner.support,
                    OverlapRow(
                        file=path,
                        basis=f"co-change pair, {partner.support} of {partner.self_commits} commits",
                        partner=our_file,
                    ),
                )
    ranked = sorted(best.values(), key=lambda item: (-item[0], item[1].file))
    return [row for _, row in ranked[:CO_CHANGE_ROWS_PER_BRANCH]]


def _rerank(
    entry: BranchOverlapEntry,
    hub: dict[str, float],
    partners: dict[str, list[CoChangePartner]],
    theirs: frozenset[str],
) -> BranchOverlapEntry:
    """Shared files by hub score, then the history rows."""
    hits = {row.file for row in entry.rows}
    direct = sorted(entry.rows, key=lambda row: (-hub.get(row.file, 0.0), row.file))
    return replace(entry, rows=tuple(direct) + tuple(_co_change_rows(partners, theirs, hits)))


async def rank_with_index(session: AsyncSession, repo_id: str, scan: BranchScan) -> BranchOverlap:
    """The scan with its shared files ordered by the index and history rows added."""
    overlap = scan.overlap
    if not overlap.branches:
        return overlap

    hits = sorted({row.file for entry in overlap.branches for row in entry.rows})
    hub, partners = await _read_index(session, repo_id, hits, sorted(scan.ours))
    # Entry order is set by the direct hits, so adding history rows must not reorder.
    return replace(
        overlap,
        branches=tuple(
            _rerank(entry, hub, partners, scan.others.get(entry.branch, frozenset()))
            for entry in overlap.branches
        ),
    )
