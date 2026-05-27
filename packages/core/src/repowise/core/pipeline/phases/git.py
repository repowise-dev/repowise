"""Pipeline git phase.

Extracted from the former monolithic ``orchestrator.py``; ``run_pipeline`` (in
orchestrator.py) imports these phase functions. No CLI/click/rich imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from repowise.core.ingestion.git_indexer import GitIndexTier
from repowise.core.pipeline.progress import ProgressCallback

from ._common import _phase_done

logger = structlog.get_logger(__name__)


async def _run_git_indexing(
    repo_path: Path,
    *,
    commit_depth: int,
    follow_renames: bool,
    tier: GitIndexTier = GitIndexTier.FULL,
    progress: ProgressCallback | None,
) -> tuple[Any | None, list[dict], dict[str, dict]]:
    """Run git history indexing.

    Returns (git_summary, git_metadata_list, git_meta_map).
    """
    try:
        from repowise.core.ingestion.git_indexer import GitIndexer

        git_indexer = GitIndexer(
            repo_path,
            commit_limit=commit_depth,
            follow_renames=follow_renames,
            tier=tier,
        )

        def _on_start(total: int) -> None:
            if progress:
                progress.on_phase_start("git", total)

        def _on_file_done() -> None:
            if progress:
                progress.on_item_done("git")

        def _on_co_change_start(total: int) -> None:
            if progress:
                progress.on_phase_start("co_change", total)

        def _on_commit_done() -> None:
            if progress:
                progress.on_item_done("co_change")

        def _on_co_change_done() -> None:
            # Stop the co_change timer the moment accumulation finishes;
            # otherwise the recorded duration also includes the parallel
            # per-file git walk that keeps running afterwards (audit #29).
            _phase_done(progress, "co_change")

        git_summary, git_metadata_list = await git_indexer.index_repo(
            "",
            on_start=_on_start,
            on_file_done=_on_file_done,
            on_co_change_start=_on_co_change_start,
            on_commit_done=_on_commit_done,
            on_co_change_done=_on_co_change_done,
        )
        git_meta_map = {m["file_path"]: m for m in git_metadata_list}
        _phase_done(progress, "git")
        # co_change phase already closed inside the done-callback above;
        # call again only as a safety-net in case the callback was never
        # invoked (e.g. co-change skipped early). PhaseTimingRecorder
        # ignores done-without-start so this is a no-op in the happy path.
        _phase_done(progress, "co_change")
        return git_summary, git_metadata_list, git_meta_map
    except Exception as exc:
        if progress:
            progress.on_message("warning", f"Git indexing skipped: {exc}")
        _phase_done(progress, "git")
        _phase_done(progress, "co_change")
        return None, [], {}
