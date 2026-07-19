"""Per-file bug-fix events, ready for the ``fix_events`` table.

``prior_defect_count`` collapses a repo's fix history into one number per file.
This module keeps the history itself: one record per fix-commit x file, carrying
the diff shape, the old-side line ranges the fix replaced, its changed LOC, and
- for ``code_fix`` rows - the ranked bug-introducing candidates :mod:`szz` traced.

Two entry points, one for each half of the indexing lifecycle:

* :func:`build_fix_events` runs over a whole :class:`~.prior_defects.FixWalk`,
  which is what a full index produces for the trailing defect window;
* the same function over a walk narrowed by ``skip_shas`` is the incremental
  path, so an update traces only the fix commits it has never seen.

Rows are stored undecayed with their ``committed_at``. Nothing here weighs
recency: that is a read-time decision, so a half-life change never costs a
reindex.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import structlog

from .fix_shape import is_code_path
from .prior_defects import FixCommit, FixWalk
from .szz import SzzTracer

logger = structlog.get_logger(__name__)

__all__ = ["build_fix_events"]

# The blame pass is subprocess-bound, so it scales with workers rather than
# cores. Matches the per-file git workers the indexer already runs.
_TRACE_WORKERS = 20


def build_fix_events(walk: FixWalk, get_repo: Any) -> list[dict]:
    """Rows for every fix-commit x file in *walk*, SZZ-traced where it applies.

    *get_repo* is a callable returning a gitpython ``Repo`` usable from the
    calling thread (blame runs across a worker pool). An update passes a walk
    already narrowed to the fix commits it has never seen
    (:func:`~.prior_defects.collect_fix_commits`'s ``skip_shas``), so nothing is
    re-blamed.

    Blame runs only for ``code_fix`` commits on production-code paths: tests,
    docs and config carry no defect the ``prior_defect`` signal cares about, and
    blaming them would be the expensive way to learn nothing.
    """
    if not walk.fixes:
        return []

    tracer = SzzTracer(get_repo)
    units = [(fix, path) for fix in walk.fixes for path in fix.paths]

    with ThreadPoolExecutor(max_workers=_TRACE_WORKERS, thread_name_prefix="szz") as pool:
        # ``map`` re-raises the first failure and discards every other result,
        # so isolation lives inside the mapped call: one unlucky file must not
        # cost the whole window's events.
        rows = [row for row in pool.map(lambda unit: _safe_row(tracer, *unit), units) if row]

    # Deterministic order so a replayed capture writes the same rows in the same
    # sequence as a fresh one.
    rows.sort(key=lambda row: (row["fix_sha"], row["file_path"]))
    return rows


def _safe_row(tracer: SzzTracer, fix: FixCommit, path: str) -> dict | None:
    try:
        return _build_row(tracer, fix, path)
    except Exception as exc:
        logger.debug("fix_event_row_failed", fix=fix.sha[:12], path=path, error=str(exc))
        return None


def _build_row(tracer: SzzTracer, fix: FixCommit, path: str) -> dict:
    diff = fix.files.get(path)
    old_ranges = list(diff.old_ranges) if diff is not None else []
    changed_loc = len(diff.removed) + len(diff.added) if diff is not None else 0

    candidates: list[dict] = []
    if fix.shape_kind == "code_fix" and diff is not None and is_code_path(path):
        try:
            candidates = [c.as_dict() for c in tracer.trace_file(fix.sha, path, diff)]
        except Exception as exc:
            # A tracing failure must never cost the event row itself: the shape
            # and ranges are still true, only the attribution is missing.
            logger.debug("szz_trace_failed", fix=fix.sha[:12], path=path, error=str(exc))

    return {
        "fix_sha": fix.sha,
        "file_path": path,
        "shape_kind": fix.shape_kind,
        "old_ranges_json": json.dumps([list(r) for r in old_ranges]),
        "changed_loc": changed_loc,
        "inducing_shas_json": json.dumps(candidates),
        "committed_at": _as_datetime(fix.ts),
    }


def _as_datetime(ts: int) -> datetime | None:
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
