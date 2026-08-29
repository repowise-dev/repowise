"""Repo-wide co-change accumulation + change entropy (FULL-tier signals).

A single ``git log --name-only`` walk feeds two history signals at once:

* **Co-change** — decay-weighted co-occurrence pairs across tracked files,
  each carrying its raw shared-commit count and both files' commit totals.
* **Change entropy** — Hassan's History Complexity Metric (2009), capturing
  how scattered each file's changes are over time.

Both are derived from the same commit iteration so the FULL tier (and the
backfill worker) pay for only one ``git log`` subprocess. The ESSENTIAL tier
defers the whole walk; absent fields are treated as "no signal" downstream.
"""

from __future__ import annotations

import heapq
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
    _MAX_FILES_PER_COMMIT_FOR_COCHANGE,
    _MAX_FILES_PER_COMMIT_FOR_ENTROPY,
    _MAX_PARTNERS_PER_FILE,
    _MIN_CO_CHANGE_SUPPORT,
)

logger = structlog.get_logger(__name__)

__all__ = ["compute_co_changes_and_entropy"]


def compute_co_changes_and_entropy(
    repo: Any,
    all_files: set[str],
    commit_limit: int = _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
    max_partners: int = _MAX_PARTNERS_PER_FILE,
    on_commit_done: Callable[[], None] | None = None,
    on_co_change_start: Callable[[int], None] | None = None,
    as_of_ts: float | None = None,
) -> tuple[dict[str, list[dict]], dict[str, float]]:
    """Walk recent commits once, returning ``(co_changes, change_entropy)``.

    Uses a single ``git log --name-only`` call instead of spawning one
    ``git diff`` subprocess per commit — O(1) processes vs O(commit_limit).

    **Co-change** applies exponential temporal decay so recent co-changes weigh
    more than ancient ones, and divides each commit's weight by ``n - 1`` so a
    pair carries the same mass whether it was seen alone or alongside a hundred
    other files. Without that, a commit contributes ``O(n^2)`` pairs at full
    weight and wide commits drown the signal.

    Each pair also keeps ``frequency`` (shared commits, undecayed) and both
    files' commit totals from this same walk, so a caller can state a
    directional confidence without borrowing a denominator computed over a
    different window. Pairs below ``_MIN_CO_CHANGE_SUPPORT`` shared commits are
    dropped, then each file keeps its ``max_partners`` strongest — both
    scale-free, unlike a cutoff on the weight, which shifts whenever the
    weighting does.

    ``on_co_change_start(total)`` is called once with the actual number of
    commits found; ``on_commit_done()`` after each commit block. Both run from a
    thread-pool thread; callers must ensure thread safety.

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
    pair_support: defaultdict[tuple[str, str], int] = defaultdict(int)
    pair_last_date: dict[tuple[str, str], int] = {}  # pair → latest Unix ts
    file_commits: defaultdict[str, int] = defaultdict(int)
    entropy_scores: defaultdict[str, float] = defaultdict(float)
    # Anchor the decay reference to the repo's most recent commit (passed by the
    # orchestrator) rather than wall-clock time, so the decay is deterministic
    # and historical-checkout-correct (mirrors file_history's as_of_ts). Falls
    # back to wall clock when not supplied.
    now_ts = as_of_ts if as_of_ts is not None else time.time()

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
        # Counted before the pair guard: a commit where a file changed alone is
        # still one of its commits, and it is the denominator that decides
        # whether the file ever changes without its partner.
        for path in current:
            file_commits[path] += 1
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
        # Split the commit's weight across the files it touched, so a pair from
        # a two-file commit outweighs one from a fifty-file commit.
        pair_weight = weight / (n - 1)
        sorted_files = sorted(current)
        for i in range(len(sorted_files)):
            for j in range(i + 1, len(sorted_files)):
                pair = (sorted_files[i], sorted_files[j])
                pair_scores[pair] += pair_weight
                pair_support[pair] += 1
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

    # Keep each file's strongest partners, via a bounded min-heap per file so
    # the persisted column stays linear in the file count.
    kept: defaultdict[str, list[tuple[float, str]]] = defaultdict(list)

    def _offer(owner: str, other: str, score: float) -> None:
        heap = kept[owner]
        if len(heap) < max_partners:
            heapq.heappush(heap, (score, other))
        elif score > heap[0][0]:
            heapq.heapreplace(heap, (score, other))

    for pair, score in pair_scores.items():
        if pair_support[pair] < _MIN_CO_CHANGE_SUPPORT:
            continue
        a, b = pair
        _offer(a, b, score)
        _offer(b, a, score)

    result: dict[str, list[dict]] = {}
    for owner, heap in kept.items():
        records = []
        for score, other in heap:
            pair = (owner, other) if owner < other else (other, owner)
            last_ts = pair_last_date.get(pair, 0)
            records.append(
                {
                    "file_path": other,
                    "co_change_count": round(score, 4),
                    "frequency": pair_support[pair],
                    "self_commits": file_commits[owner],
                    "partner_commits": file_commits[other],
                    "last_co_change": (
                        datetime.fromtimestamp(last_ts, tz=UTC).strftime("%Y-%m-%d")
                        if last_ts > 0
                        else None
                    ),
                }
            )
        records.sort(key=lambda x: x["co_change_count"], reverse=True)
        result[owner] = records

    entropy = {fp: round(score, 6) for fp, score in entropy_scores.items() if score > 0.0}

    logger.debug(
        "co_change_computed",
        commits=actual_commits,
        tracked_files=len(all_files),
        pairs_considered=len(pair_scores),
        pairs_above_support=sum(1 for c in pair_support.values() if c >= _MIN_CO_CHANGE_SUPPORT),
        files_with_partners=len(result),
        files_with_entropy=len(entropy),
        min_support=_MIN_CO_CHANGE_SUPPORT,
        max_partners=max_partners,
        commit_limit=commit_limit,
    )

    return result, entropy
