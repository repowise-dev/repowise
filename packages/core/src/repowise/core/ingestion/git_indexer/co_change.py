"""Repo-wide co-change accumulation + change entropy (FULL-tier signals).

A single ``git log --name-status`` walk feeds two history signals at once:

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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from ...co_change import canonical_pair
from ._constants import (
    _CO_CHANGE_COMMIT_DECAY_TAU,
    _CO_CHANGE_DECAY_TAU,
    _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
    _MAX_FILES_PER_COMMIT_FOR_COCHANGE,
    _MAX_FILES_PER_COMMIT_FOR_ENTROPY,
    _MAX_PARTNERS_PER_FILE,
    _MIN_CO_CHANGE_SUPPORT,
)
from .records import RenameTrail, name_status_path

logger = structlog.get_logger(__name__)

__all__ = ["CoChangeWalk", "compute_co_changes_and_entropy"]


@dataclass
class CoChangeWalk:
    """Everything one ``git log --name-status`` walk yields, keyed by file path.

    A record rather than a tuple, like ``PriorDefects`` and ``FixWalk`` in the
    orchestrator that consumes this, because the walk keeps acquiring signals.

    ``partners`` is the truncated top-``max_partners`` list that reaches
    ``co_change_partners_json``; its length is a storage cap, not a quantity.
    ``partner_count`` and ``partner_mass`` are measured over every qualifying
    partner before that truncation — the count for a human to read, the decayed
    mass for ranking, since only the mass can fall again.
    """

    partners: dict[str, list[dict]] = field(default_factory=dict)
    entropy: dict[str, float] = field(default_factory=dict)
    partner_count: dict[str, int] = field(default_factory=dict)
    partner_mass: dict[str, float] = field(default_factory=dict)


def _partner_records(
    kept: dict[str, list[tuple[float, str]]],
    pair_support: dict[tuple[str, str], int],
    pair_last_date: dict[tuple[str, str], int],
    file_commits: dict[str, int],
) -> dict[str, list[dict]]:
    """The persisted partner list per file, strongest first.

    ``self_commits`` / ``partner_commits`` are each file's commit total over
    this same walk, so a reader can state a directional confidence without
    borrowing a denominator computed over a different window.
    """
    out: dict[str, list[dict]] = {}
    for owner, heap in kept.items():
        records = []
        for score, other in heap:
            pair = canonical_pair(owner, other)
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
        out[owner] = records
    return out


def compute_co_changes_and_entropy(
    repo: Any,
    all_files: set[str],
    commit_limit: int = _DEFAULT_CO_CHANGE_COMMIT_LIMIT,
    max_partners: int = _MAX_PARTNERS_PER_FILE,
    on_commit_done: Callable[[], None] | None = None,
    on_co_change_start: Callable[[int], None] | None = None,
    as_of_ts: float | None = None,
) -> CoChangeWalk:
    """Walk recent commits once, returning every history signal it yields.

    Uses a single ``git log --name-status`` call instead of spawning one
    ``git diff`` subprocess per commit — O(1) processes vs O(commit_limit).
    Its rename rows file each older commit under the path at HEAD, so a file
    keeps its co-change partners and entropy across a rename, like its churn.

    **Co-change** applies exponential decay so recent co-changes weigh more
    than ancient ones, and divides each commit's weight by ``n - 1`` so a pair
    carries the same mass whether it was seen alone or alongside a hundred
    other files. Without that, a commit contributes ``O(n^2)`` pairs at full
    weight and wide commits drown the signal.

    That decay is measured in COMMITS, not days: the exponent is the commit's
    position in this newest-first walk (see ``CO_CHANGE_COMMIT_DECAY_TAU``).
    Change entropy keeps the calendar clock it was calibrated on, because its
    consumer gates on a percentile that a near-uniform haircut barely reorders.

    ``partner_count`` and ``partner_mass`` are accumulated over every pair that
    clears ``_MIN_CO_CHANGE_SUPPORT``, BEFORE the ``max_partners`` truncation.

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
    partner_count: defaultdict[str, int] = defaultdict(int)
    partner_mass: defaultdict[str, float] = defaultdict(float)
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
            "--name-status",
            "--no-merges",
            "--format=%x00%ct",
        )
    except Exception:
        return CoChangeWalk()

    actual_commits = raw.count("\x00")
    if on_co_change_start is not None:
        on_co_change_start(actual_commits)

    current: set[str] = set()
    moved: list[tuple[str, str]] = []
    renames = RenameTrail()
    current_ts: int = 0
    # Position in the newest-first walk (0 is HEAD): the pair decay's clock.
    current_ordinal: int = 0
    commits_seen: int = 0

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
        # Calendar clock, for change entropy only.
        weight = math.exp(-age_days / _CO_CHANGE_DECAY_TAU)
        # Commit clock, for the co-change pair mass.
        pair_decay = math.exp(-current_ordinal / _CO_CHANGE_COMMIT_DECAY_TAU)

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
        pair_weight = pair_decay / (n - 1)
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
            for old_path, new_path in moved:
                renames.record(old_path, new_path)
            current = set()
            moved = []
            ts_part = line.lstrip("\x00").strip()
            try:
                current_ts = int(ts_part)
            except (ValueError, TypeError):
                current_ts = 0
            current_ordinal = commits_seen
            commits_seen += 1
            if on_commit_done is not None:
                on_commit_done()
        elif line.strip():
            path, renamed_from = name_status_path(line)
            if renamed_from:
                moved.append((renamed_from, path))
            path = renames.resolve(path)
            if path in all_files:
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
        # Past the support filter, before the heap: breadth over every partner.
        partner_count[a] += 1
        partner_count[b] += 1
        partner_mass[a] += score
        partner_mass[b] += score
        _offer(a, b, score)
        _offer(b, a, score)

    result = _partner_records(kept, pair_support, pair_last_date, file_commits)

    entropy = {fp: round(score, 6) for fp, score in entropy_scores.items() if score > 0.0}

    logger.debug(
        "co_change_computed",
        commits=actual_commits,
        tracked_files=len(all_files),
        pairs_considered=len(pair_scores),
        pairs_above_support=sum(1 for c in pair_support.values() if c >= _MIN_CO_CHANGE_SUPPORT),
        files_with_partners=len(result),
        files_with_entropy=len(entropy),
        # Logged so the truncation is visible rather than silent.
        files_at_partner_cap=sum(1 for recs in result.values() if len(recs) >= max_partners),
        min_support=_MIN_CO_CHANGE_SUPPORT,
        max_partners=max_partners,
        commit_limit=commit_limit,
    )

    return CoChangeWalk(
        partners=result,
        entropy=entropy,
        partner_count=dict(partner_count),
        partner_mass={fp: round(mass, 6) for fp, mass in partner_mass.items()},
    )
