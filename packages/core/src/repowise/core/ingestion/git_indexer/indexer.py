"""GitIndexer — orchestrates per-file history + co-change accumulation.

The class wires together the tier modules: :mod:`file_history` for per-file
metadata, :mod:`co_change` for the repo-wide pair walk, and :mod:`enrich` for
percentiles. The :class:`~.tiers.GitIndexTier` passed at construction decides
which expensive signals run (blame, co-change); ESSENTIAL skips both.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from ._constants import (
    _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
    _DEFAULT_CO_CHANGE_MIN_COUNT,
    _DEFAULT_COMMIT_LIMIT,
    _FILE_INDEX_TIMEOUT_SECS,
)
from .co_change import compute_co_changes, compute_co_changes_and_entropy
from .enrich import compute_percentiles
from .file_history import index_file
from .prior_defects import compute_prior_defects
from .records import GitIndexSummary, _CommitRec, _should_skip_index
from .tiers import GitIndexTier

logger = structlog.get_logger(__name__)

__all__ = ["GitIndexer"]


class GitIndexer:
    """Mines git history into the git_metadata table.

    Uses gitpython (already a dependency) for git operations.
    Parallelizes per-file git log calls with asyncio.Semaphore(20).

    Non-blocking: if git is unavailable or repo has no history, log a warning
    and return an empty summary. All downstream features degrade gracefully.

    *tier* selects indexing depth: ``FULL`` (default) preserves historical
    behaviour; ``ESSENTIAL`` skips per-file blame and the co-change walk for
    fast large-repo indexing (backfill the rest later — see :mod:`backfill`).
    """

    def __init__(
        self,
        repo_path: str | Path,
        *,
        commit_limit: int | None = None,
        follow_renames: bool = False,
        tier: GitIndexTier = GitIndexTier.FULL,
    ) -> None:
        self.repo_path = Path(repo_path)
        self.commit_limit = commit_limit or _DEFAULT_COMMIT_LIMIT
        self.follow_renames = follow_renames
        self.tier = tier

    async def index_repo(
        self,
        repo_id: str,
        on_start: Callable[[int], None] | None = None,
        on_file_done: Callable[[], None] | None = None,
        on_commit_done: Callable[[], None] | None = None,
        on_co_change_start: Callable[[int], None] | None = None,
        on_co_change_done: Callable[[], None] | None = None,
    ) -> tuple[GitIndexSummary, list[dict]]:
        """Full index of all tracked files. Returns summary + list of metadata
        dicts ready for bulk upsert.

        Optional progress callbacks (all thread-safe, called from the event
        loop or executor threads):
          on_start(total)    — fired once with the total number of tracked files
          on_file_done()     — fired after each file is indexed
          on_co_change_start(total) — fired once with actual commit count for
                                       co-change analysis
          on_commit_done()   — fired after each commit is processed during
                               co-change analysis
          on_co_change_done() — fired the moment co-change accumulation
                                completes (BEFORE per-file git indexing
                                finishes).
        """
        start = time.monotonic()
        repo = self._get_repo()
        if repo is None:
            return GitIndexSummary(0, 0, 0, 0.0), []

        tracked_files = self._get_tracked_files(repo)
        if not tracked_files:
            return GitIndexSummary(0, 0, 0, 0.0), []

        # Only run expensive per-file indexing (git log + blame) on code files.
        indexable_files = [fp for fp in tracked_files if not _should_skip_index(fp)]

        if on_start is not None:
            on_start(len(indexable_files))

        from concurrent.futures import ThreadPoolExecutor

        executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="git-idx")
        semaphore = asyncio.Semaphore(20)
        loop = asyncio.get_event_loop()

        # Build a repo-wide commit index in ONE git log subprocess when
        # rename-tracking is off (the default). Each per-file worker then reads
        # its commits from this shared dict instead of spawning its own
        # ``git log -- <file>`` — turning O(files) process spawns into O(1).
        commit_index: dict[str, list[_CommitRec]] = {}
        commit_sink: list[dict] = []
        if not self.follow_renames:
            from ..git_commit_index import load_commit_index

            commit_index = load_commit_index(
                repo,
                self.commit_limit,
                set(indexable_files),
                commit_sink=commit_sink,
            )

        include_blame = self.tier.includes_blame
        as_of_ts = self._resolve_as_of_ts(repo, commit_index)

        def _index_one_sync(file_path: str) -> dict:
            """Use a per-thread Repo to avoid shared-handle issues on Windows."""
            try:
                import git as gitpython

                thread_repo = gitpython.Repo(self.repo_path, search_parent_directories=True)
                try:
                    precomputed = commit_index.get(file_path) if commit_index else None
                    return index_file(
                        thread_repo,
                        file_path,
                        repo_path=self.repo_path,
                        commit_limit=self.commit_limit,
                        follow_renames=self.follow_renames,
                        include_blame=include_blame,
                        precomputed_commits=precomputed,
                        as_of_ts=as_of_ts,
                    )
                finally:
                    thread_repo.close()
            except Exception:
                return {"file_path": file_path}

        async def index_one(file_path: str) -> dict:
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(executor, _index_one_sync, file_path),
                        timeout=_FILE_INDEX_TIMEOUT_SECS,
                    )
                except TimeoutError:
                    logger.debug(
                        "Git indexing timed out for file — using partial data",
                        path=file_path,
                        timeout=_FILE_INDEX_TIMEOUT_SECS,
                    )
                    result = {"file_path": file_path}
                except Exception as exc:
                    logger.debug("Git indexing failed for file", path=file_path, error=str(exc))
                    result = {"file_path": file_path}
                if on_file_done is not None:
                    on_file_done()
                return result

        file_tasks = [index_one(fp) for fp in indexable_files]

        async def _co_change_task() -> tuple[dict[str, list[dict]], dict[str, float]]:
            # ESSENTIAL tier defers co-change entirely (the expensive repo-wide
            # walk) — return empty and let a FULL backfill fill it in. Change
            # entropy rides the same walk, so it's deferred together.
            if not self.tier.includes_co_change:
                if on_co_change_done is not None:
                    with contextlib.suppress(Exception):
                        on_co_change_done()
                return {}, {}
            result = await loop.run_in_executor(
                executor,
                compute_co_changes_and_entropy,
                repo,
                set(tracked_files),
                max(self.commit_limit, _DEFAULT_CO_CHANGE_COMMIT_LIMIT),
                _DEFAULT_CO_CHANGE_MIN_COUNT,
                on_commit_done,
                on_co_change_start,
                as_of_ts,
            )
            if on_co_change_done is not None:
                with contextlib.suppress(Exception):
                    on_co_change_done()
            return result

        metadata_list, (co_changes, change_entropy) = await asyncio.gather(
            asyncio.gather(*file_tasks, return_exceptions=True),
            _co_change_task(),
        )

        # Abandon any timed-out threads immediately instead of letting
        # asyncio.run() block for minutes during default-executor cleanup.
        executor.shutdown(wait=False, cancel_futures=True)

        results: list[dict] = []
        for r in metadata_list:
            if isinstance(r, Exception):
                logger.warning("Failed to index file", error=str(r))
            else:
                results.append(r)

        # Prior-defect counts: one dedicated windowed git-log pass (NOT the
        # depth-capped commit index, which under-counts the busiest files —
        # exactly the ones this signal flags). Bounded to the trailing window,
        # so it's cheap regardless of total repo age and leakage-free at T0.
        prior_defects: dict[str, int] = {}
        try:
            prior_defects = compute_prior_defects(repo, set(indexable_files), as_of_ts=as_of_ts)
        except Exception as exc:
            logger.debug("prior_defect_pass_failed", error=str(exc))

        # Merge co-change partners + change entropy + prior defects into metadata.
        for meta in results:
            fp = meta["file_path"]
            if fp in co_changes:
                meta["co_change_partners_json"] = json.dumps(co_changes[fp])
            if fp in change_entropy:
                meta["change_entropy"] = change_entropy[fp]
            if fp in prior_defects:
                meta["prior_defect_count"] = prior_defects[fp]

        compute_percentiles(results)

        # Per-commit rows + just-in-time change-risk, built in-memory from the
        # commit-index walk's already-parsed diffs (no extra git pass). Empty
        # in rename-tracking mode (no batched commit index) and failure-isolated
        # so a change_risk hiccup never breaks file-level git metadata.
        commit_rows: list[dict] = []
        if commit_sink:
            try:
                from .commit_rows import build_commit_rows

                commit_rows = build_commit_rows(commit_sink)
            except Exception as exc:
                logger.debug("commit_rows_build_failed", error=str(exc))

        duration = time.monotonic() - start
        hotspots = sum(1 for m in results if m.get("is_hotspot", False))
        stable = sum(1 for m in results if m.get("is_stable", False))

        summary = GitIndexSummary(
            files_indexed=len(results),
            hotspots=hotspots,
            stable_files=stable,
            duration_seconds=duration,
            commit_rows=commit_rows,
        )
        repo.close()

        logger.info(
            "Git indexing complete",
            tier=self.tier.value,
            files=summary.files_indexed,
            hotspots=summary.hotspots,
            stable=summary.stable_files,
            duration=f"{summary.duration_seconds:.1f}s",
        )
        return summary, results

    async def index_changed_files(self, changed_file_paths: list[str]) -> list[dict]:
        """Incremental update: re-index only changed files."""
        repo = self._get_repo()
        if repo is None:
            return []

        loop = asyncio.get_event_loop()
        semaphore = asyncio.Semaphore(20)
        include_blame = self.tier.includes_blame
        as_of_ts = self._resolve_as_of_ts(repo)

        def _index_one_sync(file_path: str) -> dict:
            try:
                import git as gitpython

                thread_repo = gitpython.Repo(self.repo_path, search_parent_directories=True)
                try:
                    return index_file(
                        thread_repo,
                        file_path,
                        repo_path=self.repo_path,
                        commit_limit=self.commit_limit,
                        follow_renames=self.follow_renames,
                        include_blame=include_blame,
                        as_of_ts=as_of_ts,
                    )
                finally:
                    thread_repo.close()
            except Exception:
                return {"file_path": file_path}

        async def index_one(file_path: str) -> dict:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        loop.run_in_executor(None, _index_one_sync, file_path),
                        timeout=_FILE_INDEX_TIMEOUT_SECS,
                    )
                except (TimeoutError, Exception) as exc:
                    logger.debug(
                        "Git indexing failed for changed file",
                        path=file_path,
                        error=str(exc),
                    )
                    return {"file_path": file_path}

        tasks = [index_one(fp) for fp in changed_file_paths]
        results_raw = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[dict] = []
        for r in results_raw:
            if isinstance(r, Exception):
                logger.warning("Failed to index changed file", error=str(r))
            else:
                results.append(r)

        # Recompute prior-defect counts for the changed files (same dedicated
        # windowed pass as the full index — the per-file commit list can't carry
        # this signal accurately on busy repos).
        try:
            prior_defects = compute_prior_defects(
                repo, {m["file_path"] for m in results}, as_of_ts=as_of_ts
            )
            for meta in results:
                if meta["file_path"] in prior_defects:
                    meta["prior_defect_count"] = prior_defects[meta["file_path"]]
        except Exception as exc:
            logger.debug("prior_defect_pass_failed", error=str(exc))

        repo.close()
        return results

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _resolve_as_of_ts(
        self, repo: Any, commit_index: dict[str, list[_CommitRec]] | None = None
    ) -> float | None:
        """Optional reference 'now' for recency windows (90d/30d, age, decay).

        Default (env unset) returns ``None`` → callers anchor to wall-clock
        ``now()``, preserving the live-product meaning of "churned lately /
        is_stable" as *relative to today*.

        When ``REPOWISE_GIT_WINDOW_ANCHOR`` is truthy (e.g. ``head``), anchor
        instead to the repo's most recent commit timestamp. This makes indexing
        deterministic and correct for **historical checkouts**: scoring a
        worktree at an old commit then measures the 90 days before *that* commit
        rather than an empty window in its future. Used by the defect benchmark,
        which scores repos at a past T0 — without it every windowed process
        signal (churn, entropy, co-change, congestion) is silently zero."""
        anchor = os.environ.get("REPOWISE_GIT_WINDOW_ANCHOR", "").strip().lower()
        if anchor in ("", "0", "false", "no", "now"):
            return None
        try:
            if commit_index:
                ts = max(
                    (c.ts for recs in commit_index.values() for c in recs if c.ts > 0),
                    default=0.0,
                )
                if ts > 0:
                    return float(ts)
            return float(repo.head.commit.committed_date)
        except Exception:
            return None

    def _get_repo(self) -> Any | None:
        try:
            import git as gitpython

            return gitpython.Repo(self.repo_path, search_parent_directories=True)
        except Exception as exc:
            logger.warning(
                "Git unavailable or not a repository",
                path=str(self.repo_path),
                error=str(exc),
            )
            return None

    def _get_tracked_files(self, repo: Any) -> list[str]:
        try:
            output = repo.git.ls_files()
            return [f for f in output.splitlines() if f.strip()]
        except Exception as exc:
            logger.warning("Failed to list tracked files", error=str(exc))
            return []

    # ------------------------------------------------------------------
    # Backward-compatible instance shims
    #
    # The per-tier logic now lives in module-level functions (file_history,
    # co_change, enrich) so it can be unit tested directly. These thin methods
    # preserve the historical instance API for existing callers and tests.
    # ------------------------------------------------------------------

    def _index_file(
        self,
        file_path: str,
        repo: Any,
        precomputed_commits: list[_CommitRec] | None = None,
    ) -> dict:
        return index_file(
            repo,
            file_path,
            repo_path=self.repo_path,
            commit_limit=self.commit_limit,
            follow_renames=self.follow_renames,
            include_blame=self.tier.includes_blame,
            precomputed_commits=precomputed_commits,
            as_of_ts=self._resolve_as_of_ts(repo),
        )

    def _get_blame_ownership(
        self, file_path: str, repo: Any
    ) -> tuple[str | None, str | None, float | None]:
        from .enrich import get_blame_ownership

        return get_blame_ownership(repo, file_path)

    def _is_significant_commit(self, message: str, author: str) -> bool:
        from .enrich import is_significant_commit

        return is_significant_commit(message, author)

    def _compute_co_changes(
        self,
        repo: Any,
        all_files: set[str],
        commit_limit: int = _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
        min_count: int = _DEFAULT_CO_CHANGE_MIN_COUNT,
        on_commit_done: Callable[[], None] | None = None,
        on_co_change_start: Callable[[int], None] | None = None,
    ) -> dict[str, list[dict]]:
        return compute_co_changes(
            repo, all_files, commit_limit, min_count, on_commit_done, on_co_change_start
        )

    @staticmethod
    def _compute_percentiles(metadata_list: list[dict]) -> None:
        compute_percentiles(metadata_list)
