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
* ``pairs`` -- the raw (pre-merge) pair multiset as compact path-id
  rows. Multiset, not set: ``_merge_adjacent_pairs`` accumulates
  ``token_count`` per merged pair, so multiplicity matters.
* ``total_windows`` plus the guard flags, so the incremental path can
  re-evaluate the repo-wide window budget and refuse to splice a
  truncated state.

Validity is keyed on (version, window size, limits fingerprint); any
mismatch -- or any load/save error -- degrades to a full re-detect,
which rewrites the artifact. Best-effort by design, like the token
cache next to it.
"""

from __future__ import annotations

import contextlib
import os
import pickle
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from .limits import DuplicationLimits

log = structlog.get_logger(__name__)

_INDEX_VERSION = 1
_INDEX_FILENAME = "duplication_pairs.pkl"

# One raw-pair multiset entry as a path-id row:
# (pid_a, pid_b, a_start, a_end, b_start, b_end, count).
# Counts compress the multiset: identical raw pairs (same files and line
# geometry) always merge together downstream, so multiplicity is all the
# merge stage needs from them.
PairRow = tuple[int, int, int, int, int, int, int]


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
    paths: list[str] = field(default_factory=list)
    pairs: list[PairRow] = field(default_factory=list)
    total_windows: int = 0
    window_budget_hit: bool = False
    timed_out: bool = False

    @property
    def spliceable(self) -> bool:
        """A truncated or deadline-cut state cannot be spliced safely."""
        return not (self.window_budget_hit or self.timed_out)


def load_pair_index(
    cache_dir: Path,
    window_tokens: int,
    limits: DuplicationLimits,
) -> DuplicationPairIndex | None:
    """Load and validate the artifact; ``None`` on any mismatch/error."""
    path = Path(cache_dir) / _INDEX_FILENAME
    try:
        with path.open("rb") as fh:
            payload = pickle.load(fh)
        if (
            payload.get("version") != _INDEX_VERSION
            or payload.get("window_tokens") != window_tokens
            or tuple(payload.get("limits_key", ())) != limits_fingerprint(limits)
        ):
            return None
        return DuplicationPairIndex(
            window_tokens=window_tokens,
            limits_key=limits_fingerprint(limits),
            files=payload["files"],
            nonsurvivors=payload["nonsurvivors"],
            paths=payload["paths"],
            pairs=payload["pairs"],
            total_windows=payload["total_windows"],
            window_budget_hit=payload["window_budget_hit"],
            timed_out=payload["timed_out"],
        )
    except FileNotFoundError:
        return None
    except Exception as exc:  # corrupt / unreadable -> full re-detect
        log.debug("duplication_pair_index_load_failed", error=str(exc))
        return None


def save_pair_index(cache_dir: Path, index: DuplicationPairIndex) -> None:
    """Atomically persist *index*; failures degrade to a future full run."""
    path = Path(cache_dir) / _INDEX_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _INDEX_VERSION,
            "window_tokens": index.window_tokens,
            "limits_key": index.limits_key,
            "files": index.files,
            "nonsurvivors": index.nonsurvivors,
            "paths": index.paths,
            "pairs": index.pairs,
            "total_windows": index.total_windows,
            "window_budget_hit": index.window_budget_hit,
            "timed_out": index.timed_out,
        }
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=_INDEX_FILENAME, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp_name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except Exception as exc:
        log.debug("duplication_pair_index_save_failed", error=str(exc))
