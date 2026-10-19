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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import structlog

from repowise.core.forges import detect_forge

from ._constants import (
    _DEEP_WALK_COMMIT_LIMIT,
    _DEEP_WALK_MIN_FALLBACK,
    _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
    _DEFAULT_COMMIT_LIMIT,
    _FILE_INDEX_TIMEOUT_SECS,
    _MAX_PARTNERS_PER_FILE,
)
from ._full_index import (
    CommitSample,
    build_commit_tables,
    history_coverage,
    merge_code_signals,
)
from ._incremental import (
    IdleRefresh,
    SignalPass,
    co_change_stage,
    fallback_note_agents_for,
    idle_refresh_paths,
    prior_defect_stage,
)
from .co_change import CoChangeWalk, compute_co_changes_and_entropy
from .enrich import compute_percentiles
from .file_history import DECAY_REFRESH_KEYS, _window_anchor, index_file
from .function_blame import build_blame_index
from .prior_defects import FixWalk, PriorDefects, collect_fix_commits, compute_prior_defects
from .records import (
    GitIndexSummary,
    RepoTotals,
    _CommitRec,
    _history_tier_files,
    _should_skip_index,
    _tz_offset_minutes,
    capture_repo_totals,
)
from .tiers import GitIndexTier

# Silence GitPython's _CatFileContentStream.__del__ ValueError spam.
# When git cat-file streams are GC'd after the subprocess pipe is closed,
# their __del__ tries to drain remaining bytes and hits a closed file.
# This is harmless but floods stderr with tracebacks.
try:
    from git.cmd import _CatFileContentStream

    _orig_del = _CatFileContentStream.__del__

    def _quiet_del(self: Any) -> None:
        with contextlib.suppress(ValueError, OSError):
            _orig_del(self)

    _CatFileContentStream.__del__ = _quiet_del  # type: ignore[assignment]
except Exception:
    pass  # git not installed — nothing to patch

logger = structlog.get_logger(__name__)

__all__ = ["GitIndexer", "git_worker_count"]

# Shas per ``git log --no-walk`` call when backfilling commit offsets. Each sha
# is a 40-char argv entry, and Windows caps a command line near 32k characters —
# 200 leaves generous headroom while keeping the subprocess count low.
_OFFSET_LOOKUP_CHUNK = 200

# ``REPOWISE_GIT_WINDOW_ANCHOR`` values that measure windows from wall-clock
# time instead of the indexed commit. Unset, or any other value, anchors to HEAD.
_WALL_CLOCK_ANCHORS = frozenset({"now", "0", "false", "no", "off"})

_GIT_WORKERS_ENV = "REPOWISE_GIT_WORKERS"
_MAX_GIT_WORKERS = 8
_GIT_WORKER_MEMORY_BYTES = 384 * 1024 * 1024
_GIT_MEMORY_RESERVE_BYTES = 512 * 1024 * 1024


def _available_memory_bytes() -> int | None:
    """Best-effort available physical memory without an optional dependency."""
    if os.name == "nt":
        try:
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.available_physical)
        except (AttributeError, OSError, TypeError, ValueError):
            return None
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages) * int(page_size)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _release_repo(repo: Any) -> None:
    """Release *repo*'s git subprocesses and pack maps, like ``Repo.close()``.

    ``Repo.close()`` also runs two full ``gc.collect()`` passes on Windows so a
    caller can delete the checkout. Nothing here deletes it, and by the end of
    the git stage the heap holds the whole parsed graph, so each pass costs
    seconds: one close per worker thread put ~18 s on PowerToys' critical path.
    """
    import gitdb.util

    with contextlib.suppress(Exception):
        repo.git.clear_cache()
    with contextlib.suppress(Exception):
        gitdb.util.mman.collect()


def git_worker_count(
    pending: int,
    *,
    requested: int | None = None,
    cpu_count: int | None = None,
    available_memory_bytes: int | None = None,
) -> int:
    """Resolve one deterministic CPU/memory ceiling for Git subprocess fan-out.

    ``requested`` and ``REPOWISE_GIT_WORKERS`` are safe caps, not ways around
    the resource ceiling. Invalid values are ignored with a warning.
    """
    affinity_count = getattr(os, "process_cpu_count", None)
    cpus = cpu_count or (affinity_count() if affinity_count else None) or os.cpu_count() or 1
    available = (
        available_memory_bytes
        if available_memory_bytes is not None
        else _available_memory_bytes()
    )
    memory_workers = _MAX_GIT_WORKERS
    if available is not None:
        usable = max(0, available - _GIT_MEMORY_RESERVE_BYTES)
        memory_workers = max(1, usable // _GIT_WORKER_MEMORY_BYTES)
    resource_ceiling = max(1, min(cpus, memory_workers, _MAX_GIT_WORKERS, max(1, pending)))

    override: int | None = requested
    raw_override = os.environ.get(_GIT_WORKERS_ENV) if requested is None else None
    if raw_override:
        try:
            override = int(raw_override)
        except ValueError:
            override = 0
    if override is not None and override <= 0:
        logger.warning("invalid_git_workers", value=raw_override or override)
        override = None
    return min(resource_ceiling, override) if override is not None else resource_ceiling


@dataclass(frozen=True)
class _FilePassInputs:
    """What every per-file worker of one pass reads."""

    commit_index: dict[str, list[_CommitRec]]
    fallback_files: set[str]
    history_only: set[str]
    include_blame: bool
    as_of_ts: float | None
    provenance_classifier: Any
    note_agents: dict[str, Any]
    trace_index: Any


class _ChangedScope(NamedTuple):
    """An update's file sets after the allowlist and the history tier."""

    changed: list[str]
    all_files: set[str] | None
    history_files: set[str]
    history_changed: set[str]
    co_change_files: set[str] | None


def _successful_rows(raw: list[Any], failure_message: str) -> list[dict]:
    """Keep the rows of a per-file pass; log each exception it returned."""
    rows: list[dict] = []
    for r in raw:
        if isinstance(r, Exception):
            logger.warning(failure_message, error=str(r))
        else:
            rows.append(r)
    return rows


class GitIndexer:
    """Mines git history into the git_metadata table.

    Uses gitpython (already a dependency) for git operations.
    Parallelizes per-file Git work behind one CPU/memory-aware worker ceiling.

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
        record_episodes: bool = False,
        max_workers: int | None = None,
        read_only: bool = False,
    ) -> None:
        self.repo_path = Path(repo_path)
        self.commit_limit = commit_limit or _DEFAULT_COMMIT_LIMIT
        self.follow_renames = follow_renames
        self.tier = tier
        # Off by default, and a constructor parameter rather than a call site.
        # `health` and `dead-code` build an indexer of their own to read
        # metadata, without the repo's exclude patterns; letting them write
        # episodes would make two nominally read-only commands persist rows
        # naming files the repo excludes, and those rows outlive every prune.
        self.record_episodes = record_episodes
        # Same reason: a read-only command reads the commit-window cache but
        # never rewrites it.
        self.read_only = read_only
        self.max_workers = max_workers
        # Read once: which forge's merge messages name a commit's PR or MR.
        self.forge = detect_forge(self.repo_path)

        import pathspec

        self._exclude_patterns = exclude_patterns
        self._exclude = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns or [])

    async def index_repo(
        self,
        repo_id: str,
        on_start: Callable[[int], None] | None = None,
        on_file_done: Callable[[], None] | None = None,
        on_commit_done: Callable[[], None] | None = None,
        on_co_change_start: Callable[[int], None] | None = None,
        on_co_change_done: Callable[[], None] | None = None,
        on_warning: Callable[[str], None] | None = None,
        timings: Any | None = None,
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
          on_warning(text) — reports a degraded git phase to the caller.

        ``timings`` is the run's phase table; each step below records a
        ``git.*`` row, since the stage overlaps ingestion and its tail is
        otherwise invisible.
        """
        from repowise.core.pipeline.phase_timing import timed

        start = time.monotonic()
        repo = self._get_repo()
        if repo is None:
            return GitIndexSummary(0, 0, 0, 0.0), []

        if self._skip_partial_clone_history(repo, on_warning=on_warning):
            if on_start is not None:
                on_start(0)
            with contextlib.suppress(Exception):
                repo.close()
            return GitIndexSummary(0, 0, 0, time.monotonic() - start), []

        tracked_files = self._get_tracked_files(repo)
        if not tracked_files:
            return GitIndexSummary(0, 0, 0, 0.0), []

        # Two tiers. Code files get the full per-file pass (blame, churn
        # signals, the per-file fallback walk). Every other tracked file that
        # is not vendored, a lockfile or binary gets its history tier (counts,
        # span, authors) from the shared repo-wide walk only.
        indexable_files = [fp for fp in tracked_files if not _should_skip_index(fp)]
        history_files = _history_tier_files(
            self.repo_path, tracked_files, extra_exclude_patterns=self._exclude_patterns
        )

        from concurrent.futures import ThreadPoolExecutor

        workers = git_worker_count(len(indexable_files), requested=self.max_workers)
        loop = asyncio.get_event_loop()

        commit_sink: list[dict] = []
        prov_clf = self._provenance_classifier()
        # Agent-trace records, read once and shared with both commit-index walks
        # and the line-share merge, so they load even when no walk runs.
        trace_index = self._load_trace_index(repo)
        sample = self._full_commit_index(
            repo, indexable_files, history_files, commit_sink, prov_clf, trace_index, timings
        )
        commit_index, fallback_files = sample.commits, sample.fallback_files

        # A non-code file the shared walks could not prove complete gets no row
        # (its count stays unknown) rather than a per-file walk of its own.
        history_files -= fallback_files
        if on_start is not None:
            on_start(len(indexable_files) + len(history_files))

        fallback_note_agents: dict[str, Any] = {}
        if fallback_files.intersection(indexable_files):
            from ..git_commit_index import load_git_ai_note_agents

            fallback_note_agents = load_git_ai_note_agents(repo, None)

        as_of_ts = self._resolve_as_of_ts(repo)
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="git-idx")
        get_thread_repo, close_thread_repos = self._thread_repo_pool()
        inputs = _FilePassInputs(
            commit_index=commit_index,
            fallback_files=fallback_files,
            history_only=history_files,
            include_blame=self.tier.includes_blame,
            as_of_ts=as_of_ts,
            provenance_classifier=prov_clf,
            note_agents=fallback_note_agents,
            trace_index=trace_index,
        )

        async def _co_change_task() -> CoChangeWalk:
            # ESSENTIAL tier defers co-change entirely (the expensive repo-wide
            # walk) — return empty and let a FULL backfill fill it in. Change
            # entropy rides the same walk, so it's deferred together.
            if not self.tier.includes_co_change:
                if on_co_change_done is not None:
                    with contextlib.suppress(Exception):
                        on_co_change_done()
                return CoChangeWalk()
            result = await loop.run_in_executor(
                executor,
                compute_co_changes_and_entropy,
                repo,
                set(tracked_files),
                max(self.commit_limit, _DEFAULT_CO_CHANGE_COMMIT_LIMIT),
                _MAX_PARTNERS_PER_FILE,
                on_commit_done,
                on_co_change_start,
                as_of_ts,
            )
            if on_co_change_done is not None:
                with contextlib.suppress(Exception):
                    on_co_change_done()
            return result

        async def _file_pass() -> list[Any]:
            with timed(timings, "git.files"):
                return await self._run_per_file_pool(
                    [*indexable_files, *sorted(history_files)],
                    inputs,
                    executor=executor,
                    get_thread_repo=get_thread_repo,
                    workers=workers,
                    failure_event="Git indexing failed for file",
                    timeout_event="Git indexing timed out for file — using partial data",
                    on_file_done=on_file_done,
                )

        # The fix-commit walk and the whole-history totals read nothing the
        # per-file pass produces, so they run beside it instead of after it:
        # on a large history the totals' churn walk alone is seconds of tail.
        try:
            metadata_list, walk, (fix_walk, prior_defects, repo_totals) = await asyncio.gather(
                _file_pass(),
                _co_change_task(),
                asyncio.to_thread(self._repo_wide_passes, set(indexable_files), as_of_ts, timings),
            )
        finally:
            # Abandon timed-out threads immediately instead of letting
            # asyncio.run() block for minutes during executor cleanup.
            with timed(timings, "git.release"):
                executor.shutdown(wait=False, cancel_futures=True)
                close_thread_repos()

        results = _successful_rows(metadata_list, "Failed to index file")

        # Per-file fix events ride the fix walk: the diffs are already parsed.
        # Failure-isolated, so the prior-defect counts stand on their own.
        with timed(timings, "git.fix_events"):
            fix_event_rows, built_ok = await asyncio.to_thread(self._build_fix_events, fix_walk)

        # Git-tier episodes ride the same walk. Off the event loop because it
        # writes SQLite.
        with timed(timings, "git.episodes"):
            await asyncio.to_thread(self._record_git_episodes, fix_walk)

        # History-tier rows take none of the code signals.
        code_results = [meta for meta in results if meta["file_path"] not in history_files]
        merge_code_signals(code_results, walk, prior_defects, trace_index)
        commit_rows, commit_file_rows = build_commit_tables(commit_sink)

        duration = time.monotonic() - start
        hotspots = sum(1 for m in results if m.get("is_hotspot", False))
        stable = sum(1 for m in results if m.get("is_stable", False))

        summary = GitIndexSummary(
            files_indexed=len(results),
            hotspots=hotspots,
            stable_files=stable,
            duration_seconds=duration,
            commit_rows=commit_rows,
            commit_file_rows=commit_file_rows,
            fix_event_rows=fix_event_rows,
            fix_oldest_ts=fix_walk.oldest_fix_ts,
            fix_events_built=built_ok,
            # Whole-history totals, never read off the depth-capped sample.
            repo_totals=repo_totals,
            history_coverage=history_coverage(
                indexable_files, code_results, sample, self.commit_limit, workers
            ),
        )
        _release_repo(repo)

        logger.info(
            "Git indexing complete",
            tier=self.tier.value,
            files=summary.files_indexed,
            hotspots=summary.hotspots,
            stable=summary.stable_files,
            history_coverage=summary.history_coverage.to_dict(),
            duration=f"{summary.duration_seconds:.1f}s",
        )
        return summary, results

    def _full_commit_index(
        self,
        repo: Any,
        indexable_files: list[str],
        history_files: set[str],
        commit_sink: list[dict],
        prov_clf: Any,
        trace_index: Any,
        timings: Any,
    ) -> CommitSample:
        """One repo-wide ``git log`` that every per-file worker reads its commits from.

        Turns O(files) process spawns into O(1). Rename tracking skips it, so
        every file walks its own history and non-code files get no history rows.
        """
        if self.follow_renames:
            return CommitSample({}, set(indexable_files) | history_files, set(), set(), 0, 0, 0)
        from repowise.core.pipeline.phase_timing import timed

        from ..git_commit_index import load_sampled_commit_index

        with timed(timings, "git.commit_index"):
            sample = load_sampled_commit_index(
                repo,
                self.commit_limit,
                set(indexable_files) | history_files,
                deep_limit=_DEEP_WALK_COMMIT_LIMIT,
                # A short history-tier file has no per-file walk to fall back
                # on, so any one of them is worth a deep walk.
                deep_threshold=1 if history_files else _DEEP_WALK_MIN_FALLBACK,
                commit_sink=commit_sink,
                provenance_classifier=prov_clf,
                trace_index=trace_index,
                cache_dir=self._window_cache_dir(),
                cache_writable=not self.read_only,
            )
        return CommitSample(
            sample.commits,
            sample.fallback_files,
            sample.recent_files,
            sample.deep_files,
            sample.global_commits,
            sample.deep_commits,
            sample.history_complete_through_depth,
        )

    def _window_cache_dir(self) -> Path | None:
        """Where the commit-window cache lives: the index directory, once it exists.

        A repository that has no ``.repowise`` yet is being probed, not
        indexed, and gets no cache file written beside it.
        """
        cache_dir = self.repo_path / ".repowise"
        return cache_dir if cache_dir.is_dir() else None

    async def index_changed_files(
        self,
        changed_file_paths: list[str],
        all_files: set[str] | None = None,
        co_change_sink: dict[str, list[dict]] | None = None,
        idle_decay_sink: dict[str, dict] | None = None,
        on_warning: Callable[[str], None] | None = None,
        timings: Any | None = None,
    ) -> list[dict]:
        """Incremental update: re-index only changed files.

        Mirrors ``index_repo``'s batched shape: one repo-wide commit-index
        walk feeds every per-file worker (instead of one ``git log -- <file>``
        subprocess per changed file), so the metadata an update produces is
        identical to what a fresh full index would produce, including the
        agent-provenance rollup.

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

        ``idle_decay_sink``, when provided (with ``all_files`` and a co-change
        tier), receives a decay-only partial metadata row for every *idle*
        (unchanged) file with commits in the recent window. Incremental updates
        otherwise re-score only the changed files, so idle files never get
        their time-decayed history fields (``temporal_hotspot_score``, the 90d
        churn/commit windows, ``prior_defect_count``, ``change_entropy``,
        ``co_change_partners_json``) rewritten as the anchor advances, and
        their scores could never recover. This recomputes just those fields
        off the walks already loaded here; the persist path upserts them
        field-by-field so ownership / age / authorship (correct only from the
        full init walk) are left intact. Idle history-tier files get their
        complete row instead (see ``_compute_idle_decay``).

        ``timings`` is the run's phase table; each walk below records a
        ``rebuild.git.*`` row so a slow git step names itself.
        """
        from repowise.core.pipeline.phase_timing import timed

        repo = self._get_repo()
        if repo is None:
            return []
        if self._skip_partial_clone_history(repo, on_warning=on_warning):
            with contextlib.suppress(Exception):
                repo.close()
            return []
        scope = self._changed_scope(repo, changed_file_paths, all_files)
        changed_file_paths, all_files = scope.changed, scope.all_files
        history_files, history_changed = scope.history_files, scope.history_changed

        refresh_idle = self._wants_idle_refresh(idle_decay_sink, all_files)
        target_files = (
            set(all_files) | history_files
            if refresh_idle
            else set(changed_file_paths) | history_changed
        )
        workers = git_worker_count(len(changed_file_paths), requested=self.max_workers)
        from concurrent.futures import ThreadPoolExecutor

        prov_clf = self._provenance_classifier()
        trace_index = self._load_trace_index(repo)
        commit_index, fallback_files = self._changed_commit_index(
            repo,
            target_files,
            refresh_idle,
            history_changed,
            prov_clf,
            trace_index,
            timings,
        )
        history_files -= fallback_files
        history_changed -= fallback_files
        fallback_note_agents = fallback_note_agents_for(repo, fallback_files, changed_file_paths)
        as_of_ts = self._resolve_as_of_ts(repo)
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="git-idx")
        get_thread_repo, close_thread_repos = self._thread_repo_pool()
        executor_closed = False

        def _cleanup_executor(_task: asyncio.Task | None = None) -> None:
            nonlocal executor_closed
            if executor_closed:
                return
            executor_closed = True
            executor.shutdown(wait=False, cancel_futures=True)
            close_thread_repos()

        def _abort() -> None:
            _cleanup_executor()
            with contextlib.suppress(Exception):
                repo.close()

        owner_task = asyncio.current_task()
        if owner_task is not None:
            owner_task.add_done_callback(_cleanup_executor)

        inputs = _FilePassInputs(
            commit_index=commit_index,
            fallback_files=fallback_files,
            history_only=history_changed,
            include_blame=self.tier.includes_blame,
            as_of_ts=as_of_ts,
            provenance_classifier=prov_clf,
            note_agents=fallback_note_agents,
            trace_index=trace_index,
        )
        try:
            with timed(timings, "rebuild.git.changed_files"):
                results_raw = await self._run_per_file_pool(
                    [*changed_file_paths, *sorted(history_changed)],
                    inputs,
                    executor=executor,
                    get_thread_repo=get_thread_repo,
                    workers=workers,
                    failure_event="Git indexing failed for changed file",
                )
        except BaseException:
            # Cancellation is a BaseException: release the private pool even
            # when the caller catches it and keeps its own task alive.
            _abort()
            raise

        results = _successful_rows(results_raw, "Failed to index changed file")

        # Computed before the prior-defect pass so its one walk covers them too.
        idle_paths = (
            idle_refresh_paths(
                commit_index, set(changed_file_paths) | history_changed, fallback_files
            )
            if refresh_idle
            else []
        )
        # The code signals below (prior defects, co-change) skip history-tier rows.
        code_results = [m for m in results if m["file_path"] not in history_changed]

        sp = SignalPass(repo=repo, as_of_ts=as_of_ts, timings=timings, abort=_abort)
        # Idle files are counted too, so one whose only fix aged out drops to 0.
        prior_defects = prior_defect_stage(
            sp,
            code_results,
            {m["file_path"] for m in code_results} | set(idle_paths).difference(history_files),
        )
        if self.tier.includes_co_change and all_files:
            await co_change_stage(
                self,
                sp,
                executor,
                scope.co_change_files or set(all_files),
                code_results,
                co_change_sink,
                IdleRefresh(
                    idle_decay_sink,
                    idle_paths,
                    commit_index,
                    prov_clf,
                    prior_defects,
                    history_files,
                ),
            )

        if owner_task is not None:
            owner_task.remove_done_callback(_cleanup_executor)
        _cleanup_executor()
        _release_repo(repo)
        return results

    def _wants_idle_refresh(
        self, idle_decay_sink: dict[str, dict] | None, all_files: set[str] | None
    ) -> bool:
        """Whether this update refreshes idle files' decay fields.

        Needs the repo-wide commit index (rename tracking has none) and the
        co-change walk, which supplies idle files' fresh partners and entropy.
        """
        return bool(
            idle_decay_sink is not None
            and all_files
            and not self.follow_renames
            and self.tier.includes_co_change
        )

    def _changed_commit_index(
        self,
        repo: Any,
        target_files: set[str],
        refresh_idle: bool,
        history_changed: set[str],
        prov_clf: Any,
        trace_index: Any,
        timings: Any,
    ) -> tuple[dict[str, list[_CommitRec]], set[str]]:
        """Bucket *target_files*' window commits; ``(commit_index, fallback_files)``.

        Rename tracking has no repo-wide index, so every file walks its own
        history then. An idle refresh widens *target_files* to every tracked
        file; the git subprocess is the same, only the bucketing set widens.
        """
        deep_threshold = 1 if refresh_idle or history_changed else _DEEP_WALK_MIN_FALLBACK
        if self.follow_renames:
            return {}, set(target_files)
        from repowise.core.pipeline.phase_timing import timed

        from ..git_commit_index import load_sampled_commit_index

        with timed(timings, "rebuild.git.commit_index"):
            sample = load_sampled_commit_index(
                repo,
                self.commit_limit,
                target_files,
                deep_limit=_DEEP_WALK_COMMIT_LIMIT,
                deep_threshold=deep_threshold,
                provenance_classifier=prov_clf,
                trace_index=trace_index,
                cache_dir=self._window_cache_dir(),
                cache_writable=not self.read_only,
            )
            return sample.commits, sample.fallback_files

    def _changed_scope(
        self, repo: Any, changed_file_paths: list[str], all_files: set[str] | None
    ) -> _ChangedScope:
        """Apply the full index's allowlist and history tier to an update's file sets.

        Without the allowlist an update wrote rows for a changed workflow file
        and the idle refresh minted rows for every tracked config and markup
        file. Non-code files get the history tier the full index gives them,
        drawn from the same tracked-file set (the parsed files miss .rst,
        LICENSE and the like), and the co-change walk reads that set too.
        """
        tier_paths = (self._get_tracked_files(repo) or all_files) if all_files else None
        history_files = _history_tier_files(
            self.repo_path,
            tier_paths or changed_file_paths,
            extra_exclude_patterns=self._exclude_patterns,
        )
        history_changed = history_files.intersection(changed_file_paths)
        changed = [fp for fp in changed_file_paths if not _should_skip_index(fp)]
        co_change_files = set(tier_paths) if tier_paths else None
        if all_files:
            all_files = {fp for fp in all_files if not _should_skip_index(fp)}
        return _ChangedScope(changed, all_files, history_files, history_changed, co_change_files)

    def _index_one_file(
        self, get_thread_repo: Callable[[], Any], file_path: str, inputs: _FilePassInputs
    ) -> dict:
        """Index one file on a worker thread; any failure yields a bare row.

        Uses a per-thread Repo to avoid shared-handle issues on Windows. The
        Repo is created once per worker thread and reused across files —
        constructing a fresh ``gitpython.Repo`` per file costs ~40 ms each
        (config + ref resolution), which dominated the git phase on repos
        with thousands of files.
        """
        try:
            thread_repo = get_thread_repo()
            precomputed = (
                None
                if file_path in inputs.fallback_files
                else inputs.commit_index.get(file_path, [])
            )
            return index_file(
                thread_repo,
                file_path,
                repo_path=self.repo_path,
                forge=self.forge,
                commit_limit=self.commit_limit,
                follow_renames=self.follow_renames,
                include_blame=inputs.include_blame,
                precomputed_commits=precomputed,
                as_of_ts=inputs.as_of_ts,
                provenance_classifier=inputs.provenance_classifier,
                note_agents=inputs.note_agents,
                trace_index=inputs.trace_index,
                history_only=file_path in inputs.history_only,
            )
        except Exception:
            return {"file_path": file_path}

    async def _run_per_file_pool(
        self,
        files: list[str],
        inputs: _FilePassInputs,
        *,
        executor: Any,
        get_thread_repo: Callable[[], Any],
        workers: int,
        failure_event: str,
        timeout_event: str | None = None,
        on_file_done: Callable[[], None] | None = None,
    ) -> list[Any]:
        """Index *files* on *executor*, at most *workers* at a time.

        Returns one entry per file in input order (``gather`` with
        ``return_exceptions``). A file that times out or fails yields a bare
        ``{"file_path": ...}`` row; a timeout logs *timeout_event* when given,
        else *failure_event*.
        """
        loop = asyncio.get_event_loop()
        semaphore = asyncio.Semaphore(workers)

        async def index_one(file_path: str) -> dict:
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(
                            executor, self._index_one_file, get_thread_repo, file_path, inputs
                        ),
                        timeout=_FILE_INDEX_TIMEOUT_SECS,
                    )
                except TimeoutError as exc:
                    if timeout_event is None:
                        logger.debug(failure_event, path=file_path, error=str(exc))
                    else:
                        logger.debug(
                            timeout_event, path=file_path, timeout=_FILE_INDEX_TIMEOUT_SECS
                        )
                    result = {"file_path": file_path}
                except Exception as exc:
                    logger.debug(failure_event, path=file_path, error=str(exc))
                    result = {"file_path": file_path}
                if on_file_done is not None:
                    on_file_done()
                return result

        return await asyncio.gather(*[index_one(fp) for fp in files], return_exceptions=True)

    def _compute_idle_decay(
        self,
        repo: Any,
        idle_paths: list[str],
        commit_index: dict[str, list[_CommitRec]],
        as_of_ts: float | None,
        prov_clf: Any,
        walk: CoChangeWalk,
        prior_defects: PriorDefects,
        history_files: set[str],
    ) -> dict[str, dict]:
        """Decay-only partial rows for *idle_paths* (see ``index_changed_files``).

        Runs off the event loop. For each idle file, ``index_file`` recomputes
        the window/decay churn fields from its precomputed commits (blame off →
        no git subprocess, no file stat), then the repo-wide co-change / entropy
        / prior-defect signals are overlaid. Only :data:`DECAY_REFRESH_KEYS` are
        kept so the field-wise upsert leaves full-history columns untouched. A
        signal absent from a walk resolves to its recovered baseline (``0`` /
        ``[]``) so, e.g., a file whose only fix aged out drops to zero.
        History-tier files are the exception: their row is complete, flagged
        ``history_only``, and carries no code signal.
        """
        out: dict[str, dict] = {}
        for fp in idle_paths:
            meta = index_file(
                repo,
                fp,
                repo_path=self.repo_path,
                forge=self.forge,
                commit_limit=self.commit_limit,
                follow_renames=self.follow_renames,
                include_blame=False,
                precomputed_commits=commit_index.get(fp),
                as_of_ts=as_of_ts,
                provenance_classifier=prov_clf,
                history_only=fp in history_files,
            )
            if fp in history_files:
                # The history tier comes only from this shared walk, so this is
                # the full row a fresh index writes. Persisted whole, it also
                # backfills stores indexed before non-code files had rows.
                out[fp] = meta
                continue
            meta["change_entropy"] = walk.entropy.get(fp, 0.0)
            meta["co_change_partners_json"] = json.dumps(walk.partners.get(fp, []))
            meta["co_change_partner_count"] = walk.partner_count.get(fp, 0)
            meta["co_change_mass"] = walk.partner_mass.get(fp, 0.0)
            meta["prior_defect_count"] = prior_defects.counts.get(fp, 0)
            meta["prior_defect_raw_count"] = prior_defects.raw_counts.get(fp, 0)
            out[fp] = {"file_path": fp, **{k: meta[k] for k in DECAY_REFRESH_KEYS}}
        return out

    def capture_new_commit_rows(
        self, *, since_ts: int | None = None, file_rows_sink: list[dict] | None = None
    ) -> list[dict]:
        """Build ``git_commits`` rows for commits newer than *since_ts*.

        *file_rows_sink*, when given, is extended with the matching
        ``git_commit_files`` rows from the same walk.

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
            if self._skip_partial_clone_history(repo):
                return []

            from ..git_commit_index import load_commit_index
            from .commit_rows import build_commit_file_rows, build_commit_rows

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
            rows = build_commit_rows(sink)
            files = build_commit_file_rows(sink)
            # Only after both succeed: the sink is the caller's list, and a
            # partial extend would outlive the failure that caused it.
            if file_rows_sink is not None:
                file_rows_sink.extend(files)
            return rows
        except Exception as exc:
            logger.debug("incremental_commit_rows_failed", error=str(exc))
            return []
        finally:
            repo.close()

    def list_reachable_shas(self) -> set[str] | None:
        """Shas reachable from HEAD, or ``None`` when that cannot be trusted.

        The commit walk is ``git log --no-merges`` from HEAD, so this is exactly
        the set a full index would produce. An update run on a feature branch
        captures that branch's commits; once the branch is squash-merged or
        rebased those shas stop being reachable, and nothing else prunes them.

        ``None`` means "do not prune": a shallow clone only knows part of its
        history, so every commit below the graft point would look unreachable
        and a prune would delete real rows. Any git failure degrades the same
        way, since dropping rows is not something to guess at.
        """
        repo = self._get_repo()
        if repo is None:
            return None
        try:
            if repo.git.rev_parse("--is-shallow-repository").strip() == "true":
                return None
            out = repo.git.rev_list("--no-merges", "HEAD")
        except Exception as exc:
            logger.debug("reachable_shas_failed", error=str(exc))
            return None
        finally:
            with contextlib.suppress(Exception):
                repo.close()
        shas = {line.strip() for line in out.split("\n") if line.strip()}
        # An empty result on a repo that has rows is far more likely to be a
        # broken call than a genuinely empty history.
        return shas or None

    def capture_commit_offsets(self, shas: Sequence[str]) -> dict[str, int]:
        """Map each requested sha to its commit's UTC offset in minutes.

        Backfill support for :attr:`GitCommit.committed_offset_minutes`, which
        older indexes predate.

        Looks the shas up directly (``--no-walk``) rather than walking HEAD and
        filtering: the persisted commit table can hold more rows than the
        indexer's current ``commit_limit`` (the window has moved, and co-change
        walks deeper), so a bounded walk silently leaves the oldest rows unfilled
        — which is exactly the half-filled state this backfill exists to avoid.
        Requests are chunked because every sha is an argv entry and Windows caps
        a command line at ~32k characters.

        Returns ``{}`` on total git failure; a failed chunk drops only its own
        shas. An unfilled offset degrades that row to UTC, which is what it
        already was.
        """
        if not shas:
            return {}
        repo = self._get_repo()
        if repo is None:
            return {}

        offsets: dict[str, int] = {}
        try:
            for start in range(0, len(shas), _OFFSET_LOOKUP_CHUNK):
                chunk = list(shas[start : start + _OFFSET_LOOKUP_CHUNK])
                try:
                    out = repo.git.log("--no-walk", "--format=%H %cI", *chunk)
                except Exception as exc:
                    logger.debug("commit_offsets_chunk_failed", error=str(exc))
                    continue
                for line in out.split("\n"):
                    sha, _, iso = line.strip().partition(" ")
                    minutes = _tz_offset_minutes(iso)
                    if sha and minutes is not None:
                        offsets[sha] = minutes
        finally:
            with contextlib.suppress(Exception):
                repo.close()
        return offsets

    def capture_new_fix_events(
        self, *, known_shas: set[str] | None = None
    ) -> tuple[list[dict], int, set[str], set[str]]:
        """Build ``fix_events`` rows for fix commits not already persisted.

        The incremental counterpart to the tracing ``index_repo`` runs inline,
        and the same shape as :meth:`capture_new_commit_rows`: walk the trailing
        window once, drop the fix commits *known_shas* already covers, and blame
        the rest. An update with no new fix commits does the walk and stops,
        which is why it stays inside the +1s budget.

        Returns ``(rows, oldest_fix_ts, tracked_paths, retracted_shas)``. The
        trailing values come back even when there are no new rows, because the
        caller still has three prunes to do: events that aged out of the window,
        events for files that no longer exist, and *known_shas* the fix rule no
        longer names. A fresh index produces none of them, so without the prunes
        an update drifts away from what a re-index would hold.
        """
        repo = self._get_repo()
        if repo is None:
            return [], 0, set(), set()
        try:
            tracked = {fp for fp in self._get_tracked_files(repo) if not _should_skip_index(fp)}
            walk = collect_fix_commits(
                repo,
                tracked,
                as_of_ts=self._resolve_as_of_ts(repo),
                skip_shas=known_shas,
            )
            rows, ok = self._build_fix_events(walk)
            self._record_git_episodes(walk)
            retracted = set()
            if ok and walk.shas is not None:
                retracted = set(known_shas or ()) - walk.shas
            return rows, (walk.oldest_fix_ts if ok else 0), tracked, retracted
        except Exception as exc:
            logger.debug("incremental_fix_events_failed", error=str(exc))
            return [], 0, set(), set()
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    def _repo_wide_passes(
        self, indexable_files: set[str], as_of_ts: float | None, timings: Any | None
    ) -> tuple[FixWalk, PriorDefects, RepoTotals]:
        """The fix-commit walk, its prior-defect counts, and whole-history totals.

        Runs on a worker thread beside the per-file pass, so it opens its own
        repo handle. Prior-defect counts come from one dedicated windowed
        git-log pass, NOT the depth-capped commit index, which under-counts
        the busiest files (exactly the ones this signal flags). Bounded to the
        trailing window, so it is cheap regardless of total repo age and
        leakage-free at T0.
        """
        from repowise.core.pipeline.phase_timing import timed

        prior_defects = PriorDefects()
        fix_walk = FixWalk()
        repo = self._get_repo()
        if repo is None:
            return fix_walk, prior_defects, RepoTotals()
        try:
            with timed(timings, "git.fix_walk"):
                try:
                    fix_walk = collect_fix_commits(repo, indexable_files, as_of_ts=as_of_ts)
                    prior_defects = compute_prior_defects(
                        repo, indexable_files, as_of_ts=as_of_ts, walk=fix_walk
                    )
                except Exception as exc:
                    logger.debug("prior_defect_pass_failed", error=str(exc))
            with timed(timings, "git.repo_totals"):
                totals = capture_repo_totals(repo)
        finally:
            _release_repo(repo)
        return fix_walk, prior_defects, totals

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

    def _record_git_episodes(self, walk: FixWalk) -> None:
        """Persist *walk*'s code fixes as git-tier episodes. Never raises.

        Imported lazily, like the other precedent call sites: the episode store
        is stdlib-only so hook-time readers can open it cheaply, and keeping it
        off this module's import graph is what preserves that.
        """
        if not self.record_episodes:
            return
        with contextlib.suppress(Exception):
            from ...precedent.git_episodes import record_git_episodes

            record_git_episodes(self.repo_path, walk)

    def capture_repo_totals(self, prior: Any = None) -> Any:
        """Whole-history :class:`RepoTotals` for this repo (opens its own repo).

        The incremental counterpart to the capture ``index_repo`` runs inline:
        ``repowise update`` calls this so true project age / commit / contributor
        counts stay fresh between full re-indexes. Returns an all-``None``
        ``RepoTotals`` when git is unavailable rather than raising.

        *prior* is the previously stored ``RepoTotals``; it only lets lifetime
        churn resume from its anchor rather than re-walk the history. ``None``
        (the default, and what a full index passes) forces the whole walk.
        """
        from .records import RepoTotals

        repo = self._get_repo()
        if repo is None:
            return RepoTotals()
        try:
            return capture_repo_totals(repo, prior)
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

    def _resolve_as_of_ts(self, repo: Any) -> float | None:
        """Reference 'now' for every history window (90d/30d, age, decay, blame).

        The committer date of the indexed commit (HEAD), so the same tree scores
        the same whenever it is indexed, and a historical checkout measures the
        window before *that* commit. This is the anchor the defect benchmark
        calibrates on. Full index, update, idle refresh and fix events all
        resolve it here, so they agree whenever HEAD does.

        ``REPOWISE_GIT_WINDOW_ANCHOR=now`` opts back into wall-clock time and
        returns ``None``, which every consumer reads as ``now()``. So does a
        repo whose HEAD cannot be read."""
        anchor = os.environ.get("REPOWISE_GIT_WINDOW_ANCHOR", "").strip().lower()
        if anchor in _WALL_CLOCK_ANCHORS:
            return None
        try:
            return float(repo.head.commit.committed_date)
        except Exception:
            return None

    def blame_indexes(self, file_paths: Sequence[str]) -> dict[str, Any]:
        """Per-line ``BlameIndex`` for *file_paths*, anchored as an index run
        anchors it. For a re-score that must recover what only an index built;
        files the size cap or a git error skips are left out."""
        if not file_paths:
            return {}
        repo = self._get_repo()
        if repo is None:
            return {}
        try:
            anchor = int(_window_anchor(self._resolve_as_of_ts(repo)).timestamp())
            out: dict[str, Any] = {}
            for path in file_paths:
                idx = build_blame_index(repo, path, repo_path=self.repo_path)
                if idx.lines:
                    idx.as_of_ts = anchor
                    out[path] = idx
            return out
        finally:
            with contextlib.suppress(Exception):
                repo.close()

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
                _release_repo(repo)

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

    @staticmethod
    def _partial_clone_filter(repo: Any) -> str | None:
        """Return the active partial-clone filter for a promisor remote.

        History walks use ``--numstat``, which needs blob contents. Git silently
        downloads missing blobs for a promisor remote, turning an otherwise local
        index into an unbounded network operation. Detection reads local config
        only and degrades safely when Git cannot answer.
        """
        try:
            promisor_config = repo.git.config(
                "--get-regexp",
                r"^remote\..*\.promisor$",
            )
        except Exception:
            return None

        for line in promisor_config.splitlines():
            key, separator, value = line.partition(" ")
            if not separator or value.strip().lower() not in {"true", "1", "yes", "on"}:
                continue
            parts = key.split(".")
            if len(parts) < 3:
                continue
            remote = ".".join(parts[1:-1])
            try:
                clone_filter = repo.git.config(
                    "--get",
                    f"remote.{remote}.partialclonefilter",
                ).strip()
            except Exception:
                clone_filter = ""
            return clone_filter or "promisor"
        return None

    def _skip_partial_clone_history(
        self,
        repo: Any,
        *,
        on_warning: Callable[[str], None] | None = None,
    ) -> bool:
        """Report and bound git enrichment when *repo* is a partial clone."""
        partial_filter = self._partial_clone_filter(repo)
        if partial_filter is None:
            return False

        warning = (
            "Git history skipped for partial clone "
            f"(filter: {partial_filter}) to avoid fetching missing objects; "
            "use a full clone to enable history, churn, and ownership signals."
        )
        logger.warning(
            "git_history_skipped_partial_clone",
            partial_clone_filter=partial_filter,
        )
        if on_warning is not None:
            with contextlib.suppress(Exception):
                on_warning(warning)
        return True

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
            forge=self.forge,
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

    @staticmethod
    def _compute_percentiles(metadata_list: list[dict]) -> None:
        compute_percentiles(metadata_list)
