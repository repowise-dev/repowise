"""Persisted clone-pair index for incremental duplication runs.

A full duplication pass re-derives the repo-wide raw pair set from
scratch even when one file changed, because ``duplication_pct`` is
repo-wide. The raw pairs are a pure function of (file bytes, window
size, limits): pairs between unchanged files cannot change. Persisting
them lets an incremental run splice the pair multiset instead -- drop
the contributions of buckets a changed file touches, re-verify only
those buckets, and keep everything else verbatim.

The artifact stores:

* ``files`` -- path -> content hash for every file that contributed
  windows (the detector's gate survivors). Used to detect deletions,
  to fetch unchanged files' cached token streams, and to keep their
  token-cache entries alive across incremental runs.
* ``pairs`` -- the raw (pre-merge) pair multiset, grouped by file pair
  (path ids). Multiset, not set: ``_merge_adjacent_pairs`` accumulates
  ``token_count`` per merged pair, so multiplicity matters.
* ``merged`` -- the merge stage's output, grouped the same way. Merging
  never crosses a file pair, so a splice re-merges only the file pairs
  whose raw rows moved and reuses every other group as stored; sorting
  and merging the repo's whole raw multiset was most of an update's cost
  on a large repository.
* ``total_windows`` plus the guard flags, so the incremental path can
  re-evaluate the repo-wide window budget.
* ``budget_stop`` and ``unread`` for a run the window budget cut short:
  the file collection stopped at and the files after it, so a splice can
  check that the same cut would fall in the same place.

Validity is keyed on (version, window size, limits fingerprint, analyzer
stamp); any mismatch -- or any load/save error -- degrades to a full re-detect,
which rewrites the artifact. Best-effort by design, like the token
cache next to it. The analyzer stamp stands in for the clone verifier's
code: the pairs depend on it, and the token cache no longer goes cold when
the stamp moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

from .limits import DuplicationLimits

log = structlog.get_logger(__name__)

_INDEX_VERSION = 5
_INDEX_FILENAME = "duplication_pairs.pkl"

# A file pair as path ids into ``paths``: (pid_a, pid_b).
FilePair = tuple[int, int]
# One raw-pair multiset entry within a file pair:
# (a_start, a_end, b_start, b_end, count).
# Counts compress the multiset: identical raw pairs (same files and line
# geometry) always merge together downstream, so multiplicity is all the
# merge stage needs from them.
PairRow = tuple[int, int, int, int, int]
# One merged pair within a file pair:
# (a_start, a_end, b_start, b_end, token_count), in merge order.
MergedRow = tuple[int, int, int, int, int]


def _analyzer_version() -> int:
    # Imported at call time: engine imports the duplication package.
    from ..engine import HEALTH_ANALYZER_VERSION

    return HEALTH_ANALYZER_VERSION


def limits_fingerprint(limits: DuplicationLimits) -> tuple:
    """The limit fields that change which pairs a full run emits."""
    return (
        limits.minified_avg_line_bytes,
        limits.minified_max_line_bytes,
        limits.max_tokens_per_file,
        limits.max_total_windows,
        limits.max_bucket_windows,
    )


@dataclass
class DuplicationPairIndex:
    """In-memory form of one persisted pair-index artifact."""

    window_tokens: int
    limits_key: tuple
    files: dict[str, str] = field(default_factory=dict)  # path -> content hash
    # Paths the detector considered but that contributed no windows
    # (minified, too small, over the token cap, unreadable). Tracked so
    # an incremental run doesn't mistake them for new files every time.
    nonsurvivors: set[str] = field(default_factory=set)
    # Path-id table. A splice appends new paths and leaves deleted ones
    # unreferenced, so ids stay stable; a full run rebuilds it.
    paths: list[str] = field(default_factory=list)
    pairs: dict[FilePair, list[PairRow]] = field(default_factory=dict)
    merged: dict[FilePair, list[MergedRow]] = field(default_factory=dict)
    total_windows: int = 0
    window_budget_hit: bool = False
    timed_out: bool = False
    # Where a window-budget cut fell; ``None`` when the run was not cut.
    budget_stop: str | None = None
    unread: set[str] = field(default_factory=set)
    # path -> content key of each non-survivor ("" when unreadable), so a
    # whole-repo run can tell which files moved; ``None`` when not recorded.
    nonsurvivor_keys: dict[str, str] | None = None

    @property
    def spliceable(self) -> bool:
        """A deadline-cut state, or a budget cut with no recorded position, cannot be spliced."""
        if self.timed_out:
            return False
        return not self.window_budget_hit or self.budget_stop is not None


def load_pair_index(
    cache_dir: Path,
    window_tokens: int,
    limits: DuplicationLimits,
) -> DuplicationPairIndex | None:
    """Load and validate the artifact; ``None`` on any mismatch/error."""
    path = Path(cache_dir) / _INDEX_FILENAME
    try:
        payload = load_sealed_pickle(path, domain=_INDEX_FILENAME)
        if (
            payload.get("version") != _INDEX_VERSION
            or payload.get("window_tokens") != window_tokens
            or tuple(payload.get("limits_key", ())) != limits_fingerprint(limits)
            or payload.get("analyzer_version") != _analyzer_version()
        ):
            return None
        return DuplicationPairIndex(
            window_tokens=window_tokens,
            limits_key=limits_fingerprint(limits),
            files=payload["files"],
            nonsurvivors=payload["nonsurvivors"],
            paths=payload["paths"],
            pairs=payload["pairs"],
            merged=payload["merged"],
            total_windows=payload["total_windows"],
            window_budget_hit=payload["window_budget_hit"],
            timed_out=payload["timed_out"],
            budget_stop=payload["budget_stop"],
            unread=payload["unread"],
            nonsurvivor_keys=payload.get("nonsurvivor_keys"),
        )
    except FileNotFoundError:
        return None
    except Exception as exc:  # corrupt / unsigned / unreadable -> full re-detect
        log.debug("duplication_pair_index_load_failed", error=str(exc))
        return None


def save_pair_index(cache_dir: Path, index: DuplicationPairIndex) -> None:
    """Atomically persist *index*; failures degrade to a future full run."""
    path = Path(cache_dir) / _INDEX_FILENAME
    try:
        payload = {
            "version": _INDEX_VERSION,
            "window_tokens": index.window_tokens,
            "limits_key": index.limits_key,
            "analyzer_version": _analyzer_version(),
            "files": index.files,
            "nonsurvivors": index.nonsurvivors,
            "paths": index.paths,
            "pairs": index.pairs,
            "merged": index.merged,
            "total_windows": index.total_windows,
            "window_budget_hit": index.window_budget_hit,
            "timed_out": index.timed_out,
            "budget_stop": index.budget_stop,
            "unread": index.unread,
            "nonsurvivor_keys": index.nonsurvivor_keys,
        }
        dump_sealed_pickle(path, payload, domain=_INDEX_FILENAME)
    except Exception as exc:
        log.debug("duplication_pair_index_save_failed", error=str(exc))
