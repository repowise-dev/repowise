"""Repo-wide co-change accumulation (FULL-tier signal).

A single ``git log --name-only`` walk records decay-weighted co-occurrence
pairs across all tracked files. This is the second of the two expensive
signals the ESSENTIAL tier defers; the FULL tier (and the backfill worker)
run it.
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
)

logger = structlog.get_logger(__name__)

__all__ = ["compute_co_changes"]


def compute_co_changes(
    repo: Any,
    all_files: set[str],
    commit_limit: int = _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
    min_count: int = _DEFAULT_CO_CHANGE_MIN_COUNT,
    on_commit_done: Callable[[], None] | None = None,
    on_co_change_start: Callable[[int], None] | None = None,
) -> dict[str, list[dict]]:
    """Walk recent commits and record co-occurrence pairs for tracked files.

    Uses a single ``git log --name-only`` call instead of spawning one
    ``git diff`` subprocess per commit — O(1) processes vs O(commit_limit).

    Applies exponential temporal decay so recent co-changes weigh more than
    ancient ones. ``on_co_change_start(total)`` is called once with the actual
    number of commits found; ``on_commit_done()`` after each commit block.
    Both run from a thread-pool thread; callers must ensure thread safety.
    """
    pair_scores: defaultdict[tuple[str, str], float] = defaultdict(float)
    pair_last_date: dict[tuple[str, str], int] = {}  # pair → latest Unix ts
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
        return {}

    actual_commits = raw.count("\x00")
    if on_co_change_start is not None:
        on_co_change_start(actual_commits)

    current: set[str] = set()
    current_ts: int = 0

    def _flush_commit() -> None:
        nonlocal current_ts
        if len(current) < 2:
            return
        if len(current) > _MAX_FILES_PER_COMMIT_FOR_COCHANGE:
            # Mass-edit commit — skip pair generation entirely (see constant
            # docstring). Logged at debug for traceability.
            logger.debug(
                "co_change_skip_oversized_commit",
                files_in_commit=len(current),
                threshold=_MAX_FILES_PER_COMMIT_FOR_COCHANGE,
            )
            return
        age_days = max((now_ts - current_ts) / 86400.0, 0.0)
        weight = math.exp(-age_days / _CO_CHANGE_DECAY_TAU)
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

    logger.debug(
        "co_change_computed",
        commits=actual_commits,
        tracked_files=len(all_files),
        pairs_considered=len(pair_scores),
        pairs_above_threshold=sum(1 for s in pair_scores.values() if s >= min_count),
        files_with_partners=len(result),
        min_count=min_count,
        commit_limit=commit_limit,
    )

    return dict(result)
