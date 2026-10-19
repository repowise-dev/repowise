"""Branch overlap and independent changes: who else edits these files."""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_change_risk as _pkg
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection

#: Ceiling on the branch scan, bounded so a repository with hundreds of refs
#: cannot hold the response.
_BRANCH_OVERLAP_TIMEOUT_SECONDS = 20

#: Caps on the branch-overlap block. It answers "is anyone else editing these
#: files", not "list every branch", so both lists stay short and their tails go
#: to the omission store.
_BRANCH_OVERLAP_LIMIT = 5
_BRANCH_OVERLAP_FILES_LIMIT = 10

#: Cap on the files the index cannot link. The count is already in the block's
#: summary, so the names are supporting detail and their tail is recoverable.
_UNGROUPED_FILES_LIMIT = 10


async def _independent_changes_block(
    ctx: Any,
    changed: dict[str, set[int]],
    collector: OmissionCollector,
    revspec: str | None = None,
) -> dict[str, Any] | None:
    """The changed files split into groups nothing in the index links, or ``None``.

    Silent for a single changed file and without an index: the split is a claim
    about what the index holds, so an unindexed repo makes no claim at all.
    """
    from repowise.core.analysis.independent_changes import independent_changes
    from repowise.core.git_refs import commit_file_sets
    from repowise.core.persistence.database import get_session

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None or len(changed) < 2:
        return None
    # Returns [] without a git call for anything that is not a range, so the
    # range test lives in one place rather than here as well.
    sets = await asyncio.to_thread(commit_file_sets, str(ctx.path), revspec)
    try:
        async with get_session(session_factory) as session:
            repo_id = (await _pkg._get_repo(session)).id
            result = await independent_changes(session, repo_id, list(changed), commit_sets=sets)
    except (LookupError, SQLAlchemyError):
        return None
    if result is None:
        return None
    block = result.to_dict()
    cap_collection(
        block,
        "ungrouped_files",
        block["ungrouped_files"],
        _UNGROUPED_FILES_LIMIT,
        collector,
        label=(
            f"independent_changes.ungrouped_files beyond cap={_UNGROUPED_FILES_LIMIT}"
        ),
    )
    return block


def _scan_from_trunk(path: str, files: list[str], current: str) -> Any:
    """The base lookup and the scan, both git, in one hop off the event loop.

    *current* is the reviewed change's head, so a branch reviewed from another
    worktree is the change itself, not a rival to the served checkout's branch.
    """
    from repowise.core.analysis.branch_overlap import scan_branches
    from repowise.core.git_refs import default_base

    return scan_branches(path, files, base=default_base(path), current=current)


async def _scan_overlap(ctx: Any, files: list[str], current: str = "HEAD") -> Any:
    """Run the branch scan, with the index when one opens and without when it does not."""
    from repowise.core.analysis.branch_overlap import rank_with_index
    from repowise.core.persistence.database import get_session

    path = str(ctx.path)
    scan = await asyncio.to_thread(_pkg._scan_from_trunk, path, files, current)
    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is not None:
        try:
            # The session opens only for the index step, never across the git work.
            async with get_session(session_factory) as session:
                return await rank_with_index(session, (await _pkg._get_repo(session)).id, scan)
        except (LookupError, SQLAlchemyError):
            pass
    return scan.overlap


async def _branch_overlap_block(
    ctx: Any, changed: dict[str, set[int]], collector: OmissionCollector, current: str = "HEAD"
) -> dict[str, Any] | None:
    """Other open branches editing the files this change edits, or ``None``.

    Git alone answers it, so a repo without an index still gets the block; an
    index only ranks the shared files and adds the history rows. ``None`` when
    no other branch overlaps, because an all-clear block is noise.
    """
    if not changed:
        return None
    try:
        overlap = await asyncio.wait_for(
            _pkg._scan_overlap(ctx, sorted(changed), current), timeout=_BRANCH_OVERLAP_TIMEOUT_SECONDS
        )
    except (TimeoutError, subprocess.SubprocessError, OSError):
        return None

    block = overlap.to_dict()
    if not block["branches"]:
        return None
    # Branches first: an entry the cap drops goes to the store whole, and only
    # the entries that survive can carry an index the reader can find.
    kept = cap_collection(
        block,
        "branches",
        block["branches"],
        _BRANCH_OVERLAP_LIMIT,
        collector,
        label=f"branch_overlap.branches beyond cap={_BRANCH_OVERLAP_LIMIT}",
    )
    for i, entry in enumerate(kept):
        cap_collection(
            entry,
            "files",
            entry["files"],
            _BRANCH_OVERLAP_FILES_LIMIT,
            collector,
            label=f"branch_overlap.branches[{i}].files beyond cap={_BRANCH_OVERLAP_FILES_LIMIT}",
        )
    return block
