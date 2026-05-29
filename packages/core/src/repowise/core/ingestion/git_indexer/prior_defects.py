"""Repo-wide prior-defect counting — bug-fix commits per file in a trailing window.

A file's recent bug-fix history is the single most cost-effective defect
predictor (defects cluster; Ostrand-Weyuker, Kim's "bug cache"). The
``prior_defect`` health biomarker consumes ``git_meta["prior_defect_count"]``.

This runs as its own ``git log`` pass rather than reading the shared commit
index, deliberately: that index is depth-capped (``_DEFAULT_COMMIT_LIMIT`` newest
commits repo-wide) and buckets by file, so on a busy repo a hot file's slice of
those commits silently under-counts — exactly the files this signal cares about.

It mirrors the defect benchmark's prior-defects baseline
(``lib/baselines._prior_defect_count`` → ``defect_counter.find_fix_commits`` +
``_attribute``) so the product counts exactly the commits the benchmark labels as
fixes (product == benchmark):

* resolve the window-start commit = the last commit on/before ``as_of - window``;
* walk the **topological range** ``prior_sha..head`` (every non-merge commit
  merged into HEAD since then — so feature-branch fixes count, matching the
  benchmark's ``prior_sha..t0_sha`` range, not a date-pruned ``--since`` walk);
* classify each commit subject with the shared ``is_fix_commit`` keyword rule;
* attribute a fix to every indexable file it touched.

Reachable-from-HEAD means a T0 worktree never sees post-T0 fixes → leakage-free.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from ._constants import PRIOR_DEFECT_WINDOW_DAYS, is_fix_commit
from .records import _RECORD_SEP, _extract_rename_paths

logger = structlog.get_logger(__name__)

__all__ = ["compute_prior_defects"]

# sha <US> subject, one record per commit (NUL-separated); --name-only then
# emits the touched paths on the lines that follow.
_PRIOR_LOG_FORMAT = "%x00%H%x1f%s"
_FIELD_SEP = "\x1f"


def compute_prior_defects(
    repo: Any,
    indexable_files: set[str],
    *,
    as_of_ts: float | None,
    window_days: int = PRIOR_DEFECT_WINDOW_DAYS,
) -> dict[str, int]:
    """Return ``{file_path: bug-fix-commit count}`` over the trailing window.

    *as_of_ts* anchors the window's end (the repo's HEAD-commit timestamp under
    ``REPOWISE_GIT_WINDOW_ANCHOR``, else wall-clock now). Only commits reachable
    from HEAD are walked, so a historical T0 checkout never sees post-T0 fixes.
    Files outside *indexable_files* are ignored.
    """
    now = datetime.fromtimestamp(as_of_ts, tz=UTC) if as_of_ts is not None else datetime.now(UTC)
    window_start = (now - timedelta(days=window_days)).strftime("%Y-%m-%d")

    try:
        head_sha = repo.head.commit.hexsha  # type: ignore[attr-defined]
    except Exception as exc:
        logger.debug("prior_defect_head_failed", error=str(exc))
        return {}

    # Window-start boundary: the last commit on/before (as_of - window),
    # reachable from HEAD. Mirrors the benchmark's ``resolve_t0_sha``.
    prior_sha = ""
    try:
        prior_sha = repo.git.log(  # type: ignore[attr-defined]
            head_sha, f"--before={window_start}T23:59:59", "-1", "--format=%H"
        ).strip()
    except Exception as exc:
        logger.debug("prior_defect_window_resolve_failed", error=str(exc))

    # Range: prior_sha..head (window) when the boundary resolves; otherwise the
    # repo is younger than the window → count all fixes reachable from HEAD.
    rev_range = f"{prior_sha}..{head_sha}" if prior_sha else head_sha
    try:
        raw = repo.git.log(  # type: ignore[attr-defined]
            rev_range,
            "--no-merges",
            "--name-only",
            f"--format={_PRIOR_LOG_FORMAT}",
        )
    except Exception as exc:
        logger.debug("prior_defect_log_failed", error=str(exc))
        return {}

    if not raw:
        return {}

    counts: dict[str, int] = {}
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        head, _, rest = record.partition("\n")
        _sha, _, subject = head.partition(_FIELD_SEP)
        if not is_fix_commit(subject):
            continue
        for line in rest.split("\n"):
            path = line.strip()
            if not path:
                continue
            if "=>" in path:  # rename marker {old => new}
                _old, new = _extract_rename_paths(path, set())
                path = new or path
            if path in indexable_files:
                counts[path] = counts.get(path, 0) + 1
    return counts
