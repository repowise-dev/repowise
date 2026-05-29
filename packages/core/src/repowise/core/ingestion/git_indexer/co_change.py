"""Repo-wide co-change accumulation + change entropy (FULL-tier signals).

A single ``git log --name-only`` walk feeds two history signals at once:

* **Co-change** — decay-weighted co-occurrence pairs across tracked files.
* **Change entropy** — Hassan's History Complexity Metric (2009), capturing
  how scattered each file's changes are over time.

Both are derived from the same commit iteration so the FULL tier (and the
backfill worker) pay for only one ``git log`` subprocess. The ESSENTIAL tier
defers the whole walk; absent fields are treated as "no signal" downstream.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from ._constants import (
    _CO_CHANGE_DECAY_TAU,
    _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
    _DEFAULT_CO_CHANGE_MIN_COUNT,
    _MAX_FILES_PER_COMMIT_FOR_COCHANGE,
    _MAX_FILES_PER_COMMIT_FOR_ENTROPY,
)

logger = structlog.get_logger(__name__)

__all__ = ["compute_co_changes", "compute_co_changes_and_entropy"]


def compute_co_changes_and_entropy(
    repo: Any,
    all_files: set[str],
    commit_limit: int = _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
    min_count: int = _DEFAULT_CO_CHANGE_MIN_COUNT,
    on_commit_done: Callable[[], None] | None = None,
    on_co_change_start: Callable[[int], None] | None = None,
) -> tuple[dict[str, list[dict]], dict[str, float]]:
    """Walk recent commits once, returning ``(co_changes, change_entropy)``.

    Uses a single ``git log --name-only`` call instead of spawning one
    ``git diff`` subprocess per commit — O(1) processes vs O(commit_limit).

    **Co-change** applies exponential temporal decay so recent co-changes weigh
    more than ancient ones. ``on_co_change_start(total)`` is called once with the
    actual number of commits found; ``on_commit_done()`` after each commit block.
    Both run from a thread-pool thread; callers must ensure thread safety.

    **Change entropy** adapts Hassan's History Complexity Metric: each commit is
    a one-period window whose entropy is ``log2(|F|)`` (``|F|`` = tracked files
    it touched), distributed uniformly (``1/|F|`` each) across those files with
    the same temporal decay. A file only ever changed alone (``|F| == 1``, so
    ``log2(1) == 0``) accrues no entropy; a file repeatedly caught in wide,
    scattered commits accrues a lot. Commits touching more than
    ``_MAX_FILES_PER_COMMIT_FOR_ENTROPY`` files are dropped as noise. The return
    value maps ``file_path → decayed HCM sum`` (only files with a positive sum).
    """
    pair_scores: defaultdict[tuple[str, str], float] = defaultdict(float)
    pair_last_date: dict[tuple[str, str], int] = {}  # pair → latest Unix ts
    entropy_scores: defaultdict[str, float] = defaultdict(float)
    now_ts = time.time()

    try:
        # %x00 = commit separator, %ct = committer timestamp (Unix epoch).
        raw = repo.git.log(
            f"-{commit_limit}",
            "--name-only",
            "--no-merges",
            "--format=%x00%ct",
        )
    except Exception:
        return {}, {}

    actual_commits = raw.count("\x00")
    if on_co_change_start is not None:
        on_co_change_start(actual_commits)

    current: set[str] = set()
    current_ts: int = 0

    def _flush_commit() -> None:
        nonlocal current_ts
        n = len(current)
        if n < 2:
            return
        age_days = max((now_ts - current_ts) / 86400.0, 0.0)
        weight = math.exp(-age_days / _CO_CHANGE_DECAY_TAU)

        # Change entropy (Hassan HCM). The commit-as-period entropy is
        # ``log2(n)``; each of its files gets the uniform ``1/n`` share, so the
        # per-file contribution is ``weight * log2(n) / n``. Wide mass-edit
        # commits are excluded with a tighter cap than co-change.
        if n <= _MAX_FILES_PER_COMMIT_FOR_ENTROPY:
            contribution = weight * math.log2(n) / n
            for path in current:
                entropy_scores[path] += contribution

        if n > _MAX_FILES_PER_COMMIT_FOR_COCHANGE:
            # Mass-edit commit — skip pair generation entirely (see constant
            # docstring). Logged at debug for traceability.
            logger.debug(
                "co_change_skip_oversized_commit",
                files_in_commit=n,
                threshold=_MAX_FILES_PER_COMMIT_FOR_COCHANGE,
            )
            return
        sorted_files = sorted(current)
        for i in range(len(sorted_files)):
            for j in range(i + 1, len(sorted_files)):
                pair = (sorted_files[i], sorted_files[j])
                pair_scores[pair] += weight
                if pair not in pair_last_date or current_ts > pair_last_date[pair]:
                    pair_last_date[pair] = current_ts

    for line in raw.splitlines():
        if line == "\x00" or line.startswith("\x00"):
            # Commit boundary — flush previous, parse timestamp.
            _flush_commit()
            current = set()
            ts_part = line.lstrip("\x00").strip()
            try:
                current_ts = int(ts_part)
            except (ValueError, TypeError):
                current_ts = 0
            if on_commit_done is not None:
                on_commit_done()
        else:
            path = line.strip()
            if path and path in all_files:
                current.add(path)

    _flush_commit()  # final commit

    # Build result: for each file, list partners above threshold.
    result: dict[str, list[dict]] = defaultdict(list)
    for (a, b), score in pair_scores.items():
        if score >= min_count:
            last_ts = pair_last_date.get((a, b), 0)
            last_date = (
                datetime.fromtimestamp(last_ts, tz=UTC).strftime("%Y-%m-%d")
                if last_ts > 0
                else None
            )
            result[a].append(
                {
                    "file_path": b,
                    "co_change_count": round(score, 2),
                    "last_co_change": last_date,
                }
            )
            result[b].append(
                {
                    "file_path": a,
                    "co_change_count": round(score, 2),
                    "last_co_change": last_date,
                }
            )

    # Sort partners by score descending
    for fp in result:
        result[fp].sort(key=lambda x: x["co_change_count"], reverse=True)

    entropy = {fp: round(score, 6) for fp, score in entropy_scores.items() if score > 0.0}

    logger.debug(
        "co_change_computed",
        commits=actual_commits,
        tracked_files=len(all_files),
        pairs_considered=len(pair_scores),
        pairs_above_threshold=sum(1 for s in pair_scores.values() if s >= min_count),
        files_with_partners=len(result),
        files_with_entropy=len(entropy),
        min_count=min_count,
        commit_limit=commit_limit,
    )

    return dict(result), entropy


def compute_co_changes(
    repo: Any,
    all_files: set[str],
    commit_limit: int = _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
    min_count: int = _DEFAULT_CO_CHANGE_MIN_COUNT,
    on_commit_done: Callable[[], None] | None = None,
    on_co_change_start: Callable[[int], None] | None = None,
) -> dict[str, list[dict]]:
    """Co-change-only wrapper over :func:`compute_co_changes_and_entropy`.

    Preserves the historical signature for the instance shim and existing
    tests. Production indexing calls the combined function directly so the
    single ``git log`` walk feeds both signals.
    """
    co_changes, _entropy = compute_co_changes_and_entropy(
        repo, all_files, commit_limit, min_count, on_commit_done, on_co_change_start
    )
    return co_changes
