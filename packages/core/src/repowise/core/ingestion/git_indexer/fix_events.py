"""Per-file bug-fix events, ready for the ``fix_events`` table.

``prior_defect_count`` collapses a repo's fix history into one number per file.
This module keeps the history itself: one record per fix-commit x file, carrying
the diff shape, the old-side line ranges the fix replaced, and its changed LOC.

Two entry points, one for each half of the indexing lifecycle:

* :func:`build_fix_events` runs over a whole :class:`~.prior_defects.FixWalk`,
  which is what a full index produces for the trailing defect window;
* the same function over a walk narrowed by ``skip_shas`` is the incremental
  path, so an update builds rows only for fix commits it has never seen.

Rows are stored undecayed with their ``committed_at``. Nothing here weighs
recency: that is a read-time decision, so a half-life change never costs a
reindex.

These rows once carried SZZ blame candidates naming the commit that introduced
each bug. Blame traced them at ~74.5% precision, which was never enough to name
a commit in a user-facing surface, and every consumer that would have read them
was cut before one shipped. The pass was removed rather than left running for a
column nobody reads; ``inducing_shas_json`` survives, always empty. The tracer
and its accuracy measurements are in git history.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog

from .prior_defects import FixCommit, FixWalk

logger = structlog.get_logger(__name__)

__all__ = ["build_fix_events"]


def build_fix_events(walk: FixWalk) -> list[dict]:
    """Rows for every fix-commit x file in *walk*.

    An update passes a walk already narrowed to the fix commits it has never
    seen (:func:`~.prior_defects.collect_fix_commits`'s ``skip_shas``), so
    nothing is rebuilt.
    """
    if not walk.fixes:
        return []

    rows = [
        row
        for fix in walk.fixes
        for path in fix.paths
        if (row := _safe_row(fix, path)) is not None
    ]

    # Deterministic order so a replayed capture writes the same rows in the same
    # sequence as a fresh one.
    rows.sort(key=lambda row: (row["fix_sha"], row["file_path"]))
    return rows


def _safe_row(fix: FixCommit, path: str) -> dict | None:
    """One row, or ``None``: one unlucky file must not cost the whole window."""
    try:
        return _build_row(fix, path)
    except Exception as exc:
        logger.debug("fix_event_row_failed", fix=fix.sha[:12], path=path, error=str(exc))
        return None


def _build_row(fix: FixCommit, path: str) -> dict:
    diff = fix.files.get(path)
    old_ranges = list(diff.old_ranges) if diff is not None else []
    changed_loc = len(diff.removed) + len(diff.added) if diff is not None else 0

    return {
        "fix_sha": fix.sha,
        "file_path": path,
        "shape_kind": fix.shape_kind,
        "old_ranges_json": json.dumps([list(r) for r in old_ranges]),
        "changed_loc": changed_loc,
        "inducing_shas_json": "[]",
        "committed_at": _as_datetime(fix.ts),
    }


def _as_datetime(ts: int) -> datetime | None:
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
