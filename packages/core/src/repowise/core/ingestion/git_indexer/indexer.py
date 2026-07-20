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
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from ._constants import (
    _DEEP_WALK_COMMIT_LIMIT,
    _DEEP_WALK_MIN_FALLBACK,
    _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
    _DEFAULT_CO_CHANGE_MIN_COUNT,
    _DEFAULT_COMMIT_LIMIT,
    _FILE_INDEX_TIMEOUT_SECS,
)
from .co_change import compute_co_changes, compute_co_changes_and_entropy
from .enrich import compute_percentiles
from .file_history import index_file
from .prior_defects import FixWalk, PriorDefects, collect_fix_commits, compute_prior_defects
from .records import GitIndexSummary, _CommitRec, _should_skip_index, capture_repo_totals
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
        exclude_patterns: list[str] | None = None,
    ) -> None:
        self.repo_path = Path(repo_path)
        self.commit_limit = commit_limit or _DEFAULT_COMMIT_LIMIT
        self.follow_renames = follow_renames
        self.tier = tier

        import pathspec

        self._exclude = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns or [])

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
        prov_clf = self._provenance_classifier()
        deep_index: dict[str, list[_CommitRec]] = {}
        # Agent-trace records read ONCE per index (one stat call for repos
        # without a .agent-trace/ dir). Shared with both commit-index walks so
        # the file isn't re-read per walk, and reused below for the per-file
        # line-share merge — which is why it loads even in follow_renames mode,
        # where neither commit-index walk runs.
        trace_index = self._load_trace_index(repo)
        if not self.follow_renames:
            from ..git_commit_index import load_commit_index, load_deep_commit_index

            commit_index = load_commit_index(
                repo,
                self.commit_limit,
                set(indexable_files),
                commit_sink=commit_sink,
                provenance_classifier=prov_clf,
                trace_index=trace_index,
            )

            # Files the recent window never saw would each spawn a per-file
            # ``git log`` fallback below. When there are many (deep-history
            # repos), one --skip walk over the older region replaces them;
            # files it still misses keep the per-file path.
            missed = {fp for fp in indexable_files if fp not in commit_index}
            if len(missed) >= _DEEP_WALK_MIN_FALLBACK:
                deep_index = load_deep_commit_index(
                    repo,
                    self.commit_limit,
                    missed,
                    skip=self.commit_limit,
                    deep_limit=_DEEP_WALK_COMMIT_LIMIT,
                    provenance_classifier=prov_clf,
                    trace_index=trace_index,
                )

        include_blame = self.tier.includes_blame
        as_of_ts = self._resolve_as_of_ts(repo, commit_index)
        get_thread_repo, close_thread_repos = self._thread_repo_pool()

        def _index_one_sync(file_path: str) -> dict:
            """Use a per-thread Repo to avoid shared-handle issues on Windows.

            The Repo is created once per worker thread and reused across files
            — constructing a fresh ``gitpython.Repo`` per file costs ~40 ms
            each (config + ref resolution), which dominated the git phase on
            repos with thousands of files.
            """
            try:
                thread_repo = get_thread_repo()
                precomputed = commit_index.get(file_path) if commit_index else None
                if precomputed is None:
                    precomputed = deep_index.get(file_path)
                return index_file(
                    thread_repo,
                    file_path,
                    repo_path=self.repo_path,
                    commit_limit=self.commit_limit,
                    follow_renames=self.follow_renames,
                    include_blame=include_blame,
                    precomputed_commits=precomputed,
                    as_of_ts=as_of_ts,
                    provenance_classifier=prov_clf,
                )
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
        close_thread_repos()

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
        prior_defects = PriorDefects()
        fix_walk = FixWalk()
        try:
            fix_walk = collect_fix_commits(repo, set(indexable_files), as_of_ts=as_of_ts)
            prior_defects = compute_prior_defects(
                repo, set(indexable_files), as_of_ts=as_of_ts, walk=fix_walk
            )
        except Exception as exc:
            logger.debug("prior_defect_pass_failed", error=str(exc))

        # Per-file fix events + SZZ tracing ride the same walk: the diffs are
        # already parsed, so this pass only adds blame. Off the event loop, since
        # it fans blame subprocesses out across its own pool for long enough that
        # progress callbacks would visibly stall. Failure-isolated — the counts
        # above stand on their own if tracing breaks.
        fix_event_rows, built_ok = await asyncio.to_thread(self._build_fix_events, fix_walk)

        # Per-file AI line share from the agent-trace records. Keyed by path
        # like the aggregates below, so it merges in the same pass and
        # works regardless of which commit-index walk (if any) ran.
        trace_line_shares = trace_index.line_shares() if trace_index else {}

        # Merge co-change partners + change entropy + prior defects + AI line
        # share into metadata.
        for meta in results:
            fp = meta["file_path"]
            if fp in co_changes:
                meta["co_change_partners_json"] = json.dumps(co_changes[fp])
            if fp in change_entropy:
                meta["change_entropy"] = change_entropy[fp]
            if fp in prior_defects.counts:
                meta["prior_defect_count"] = prior_defects.counts[fp]
            if fp in prior_defects.raw_counts:
                meta["prior_defect_raw_count"] = prior_defects.raw_counts[fp]
            share = trace_line_shares.get(fp)
            if share:
                meta["agent_line_count"] = share[0]
                meta["agent_line_model_json"] = json.dumps(share[1])

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
            fix_event_rows=fix_event_rows,
            fix_oldest_ts=fix_walk.oldest_fix_ts,
            fix_events_built=built_ok,
            # Whole-history totals from cheap git calls on the still-open repo —
            # true project age / commit / contributor counts for the stats page,
            # which must not read them off the depth-capped sample (issue #730).
            repo_totals=capture_repo_totals(repo),
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

    async def index_changed_files(
        self,
        changed_file_paths: list[str],
        all_files: set[str] | None = None,
        co_change_sink: dict[str, list[dict]] | None = None,
    ) -> list[dict]:
        """Incremental update: re-index only changed files.

        Mirrors ``index_repo``'s batched shape: one repo-wide commit-index
        walk feeds every per-file worker (instead of one ``git log -- <file>``
        subprocess per changed file), so the metadata an update produces is
        identical to what a fresh full index would produce — including the
        agent-provenance rollup, which the old per-file path never classified.

        ``all_files`` is the repo's full tracked-file set. When provided (and
        the tier includes co-change), the repo-wide co-change/entropy walk
        re-runs so the changed files' partners stay fresh — without it,
        ``index_file``'s empty defaults overwrite the init-computed partners
        on every update, blanking exactly the files that change most.

        ``co_change_sink``, when provided, receives the FULL per-file partner
        map that walk produces (every tracked file, not just the changed
        ones). The update path uses it to rebuild the graph's ``co_changes``
        edges for the whole repo so the update-built graph converges with the
        init-built one.
        """
        repo = self._get_repo()
        if repo is None:
            return []

        loop = asyncio.get_event_loop()
        semaphore = asyncio.Semaphore(20)
        include_blame = self.tier.includes_blame

        prov_clf = self._provenance_classifier()
        commit_index: dict[str, list[_CommitRec]] = {}
        if not self.follow_renames:
            from ..git_commit_index import load_commit_index

            commit_index = load_commit_index(
                repo,
                self.commit_limit,
                set(changed_file_paths),
                provenance_classifier=prov_clf,
            )
        as_of_ts = self._resolve_as_of_ts(repo, commit_index)
        get_thread_repo, close_thread_repos = self._thread_repo_pool()

        def _index_one_sync(file_path: str) -> dict:
            try:
                thread_repo = get_thread_repo()
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
                    provenance_classifier=prov_clf,
                )
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
        try:
            results_raw = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            close_thread_repos()

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
                fp = meta["file_path"]
                if fp in prior_defects.counts:
                    meta["prior_defect_count"] = prior_defects.counts[fp]
                if fp in prior_defects.raw_counts:
                    meta["prior_defect_raw_count"] = prior_defects.raw_counts[fp]
        except Exception as exc:
            logger.debug("prior_defect_pass_failed", error=str(exc))

        # Co-change partners + change entropy ride a repo-wide walk the
        # per-file pass cannot produce. ``index_file`` resets both fields to
        # empty defaults and the upsert overwrites rows field-by-field, so
        # skipping this walk wiped the init-computed partners for every file
        # an update touched. One ``git log --name-only`` subprocess, same as
        # the full-index path.
        if self.tier.includes_co_change and all_files:
            try:
                co_changes, change_entropy = await loop.run_in_executor(
                    None,
                    compute_co_changes_and_entropy,
                    repo,
                    set(all_files),
                    max(self.commit_limit, _DEFAULT_CO_CHANGE_COMMIT_LIMIT),
                    _DEFAULT_CO_CHANGE_MIN_COUNT,
                    None,
                    None,
                    as_of_ts,
                )
                for meta in results:
                    fp = meta["file_path"]
                    if fp in co_changes:
                        meta["co_change_partners_json"] = json.dumps(co_changes[fp])
                    if fp in change_entropy:
                        meta["change_entropy"] = change_entropy[fp]
                if co_change_sink is not None:
                    co_change_sink.update(co_changes)
            except Exception as exc:
                logger.debug("co_change_pass_failed", error=str(exc))

        repo.close()
        return results

    def capture_new_commit_rows(self, *, since_ts: int | None = None) -> list[dict]:
        """Build ``git_commits`` rows for commits newer than *since_ts*.

        The incremental counterpart to the ``commit_sink`` capture on
        ``index_repo``: walks the repo-wide commit index (one ``git log`` pass,
        bounded to commits newer than the newest already-persisted one) and
        turns the sunk commits into rows via :func:`build_commit_rows`. Empty in
        rename-tracking mode (no batched commit index — same limitation as the
        full index). Failure-isolated: returns ``[]`` on any error.
        """
        if self.follow_renames:
            return []
        repo = self._get_repo()
        if repo is None:
            return []
        try:
            from ..git_commit_index import load_commit_index
            from .commit_rows import build_commit_rows

            sink: list[dict] = []
            # Empty indexable set: we only want the full-footprint sink, not the
            # per-file bucket (which the incremental file pass already rebuilt).
            load_commit_index(
                repo,
                self.commit_limit,
                set(),
                commit_sink=sink,
                since_ts=since_ts,
                provenance_classifier=self._provenance_classifier(),
            )
            return build_commit_rows(sink)
        except Exception as exc:
            logger.debug("incremental_commit_rows_failed", error=str(exc))
            return []
        finally:
            repo.close()

    def capture_new_fix_events(
        self, *, known_shas: set[str] | None = None
    ) -> tuple[list[dict], int, set[str]]:
        """Build ``fix_events`` rows for fix commits not already persisted.

        The incremental counterpart to the tracing ``index_repo`` runs inline,
        and the same shape as :meth:`capture_new_commit_rows`: walk the trailing
        window once, drop the fix commits *known_shas* already covers, and blame
        the rest. An update with no new fix commits does the walk and stops,
        which is why it stays inside the +1s budget.

        Returns ``(rows, oldest_fix_ts, tracked_paths)``. Both trailing values
        come back even when there are no new rows, because the caller still has
        two prunes to do: events that aged out of the window, and events for
        files that no longer exist. A fresh index never produces the latter, so
        without that second prune an update accumulates rows for deleted files
        and drifts away from what a re-index would hold.
        """
        repo = self._get_repo()
        if repo is None:
            return [], 0, set()
        try:
            tracked = {fp for fp in self._get_tracked_files(repo) if not _should_skip_index(fp)}
            walk = collect_fix_commits(
                repo,
                tracked,
                as_of_ts=self._resolve_as_of_ts(repo),
                skip_shas=known_shas,
            )
            rows, ok = self._build_fix_events(walk)
            return rows, (walk.oldest_fix_ts if ok else 0), tracked
        except Exception as exc:
            logger.debug("incremental_fix_events_failed", error=str(exc))
            return [], 0, set()
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    def _build_fix_events(self, walk: FixWalk) -> tuple[list[dict], bool]:
        """Build *walk*'s fix commits into ``(rows, built_ok)``.

        Failure-isolated: this never breaks the git phase. The flag matters
        because "no rows" is ambiguous - a window with no new fixes and a pass
        that blew up look the same from the outside, and only one of them should
        let the caller prune.
        """
        if not walk.fixes:
            return [], True
        try:
            from .fix_events import build_fix_events

            return build_fix_events(walk), True
        except Exception as exc:
            logger.debug("fix_event_build_failed", error=str(exc))
            return [], False

    def capture_repo_totals(self) -> Any:
        """Whole-history :class:`RepoTotals` for this repo (opens its own repo).

        The incremental counterpart to the capture ``index_repo`` runs inline:
        ``repowise update`` calls this so true project age / commit / contributor
        counts stay fresh between full re-indexes. Returns an all-``None``
        ``RepoTotals`` when git is unavailable rather than raising.
        """
        from .records import RepoTotals

        repo = self._get_repo()
        if repo is None:
            return RepoTotals()
        try:
            return capture_repo_totals(repo)
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _provenance_classifier(self) -> Any:
        """Config-aware agent-provenance classifier, built once per index run.

        Failure-isolated: a malformed repo config falls back to the built-in
        pattern registry rather than breaking the git phase.
        """
        try:
            from .agent_provenance import classifier_from_repo_config

            return classifier_from_repo_config(self.repo_path)
        except Exception as exc:
            logger.debug("agent_provenance_classifier_failed", error=str(exc))
            from .agent_provenance import AgentProvenanceClassifier

            return AgentProvenanceClassifier()

    def _load_trace_index(self, repo: Any) -> Any:
        """Agent-trace index, loaded once per index run (one stat call when the
        repo has no ``.agent-trace/``). Failure-isolated: a broken trace file
        yields an empty index, never breaks the git phase."""
        try:
            from .agent_provenance import AgentTraceIndex

            return AgentTraceIndex.load(repo)
        except Exception as exc:
            logger.debug("agent_trace_index_failed", error=str(exc))
            from .agent_provenance import AgentTraceIndex

            return AgentTraceIndex()

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

    def _thread_repo_pool(self) -> tuple[Callable[[], Any], Callable[[], None]]:
        """Return ``(get_thread_repo, close_all)`` for per-thread Repo reuse.

        gitpython ``Repo`` handles can't be shared across threads on Windows,
        but constructing one per *file* costs ~40 ms each. This pool hands
        each worker thread its own lazily-created ``Repo`` and reuses it for
        every file that thread processes. ``close_all()`` closes every repo
        the pool created; a worker thread abandoned mid-file (timeout) will
        see its repo closed underneath it and fall into the existing
        per-file exception path, which is the same partial-data outcome the
        old per-file construction produced on failure.
        """
        tls = threading.local()
        created: list[Any] = []
        lock = threading.Lock()

        def get_thread_repo() -> Any:
            repo = getattr(tls, "repo", None)
            if repo is None:
                import git as gitpython

                repo = gitpython.Repo(self.repo_path, search_parent_directories=True)
                tls.repo = repo
                with lock:
                    created.append(repo)
            return repo

        def close_all() -> None:
            with lock:
                repos, created[:] = list(created), []
            for repo in repos:
                with contextlib.suppress(Exception):
                    repo.close()

        return get_thread_repo, close_all

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
            paths = [f for f in output.splitlines() if f.strip()]
            if self._exclude.patterns:
                paths = [p for p in paths if not self._exclude.match_file(p)]
            return paths
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
