"""Repo-wide prior-defect counting — bug-fix commits per file in a trailing window.

A file's recent bug-fix history is the single most cost-effective defect
predictor (defects cluster; Ostrand-Weyuker, Kim's "bug cache"). The
``prior_defect`` health biomarker consumes ``git_meta["prior_defect_count"]``.

This runs as its own ``git log`` pass rather than reading the shared commit
index, deliberately: that index is depth-capped (``_DEFAULT_COMMIT_LIMIT`` newest
commits repo-wide) and buckets by file, so on a busy repo a hot file's slice of
those commits silently under-counts — exactly the files this signal cares about.

The walk mirrors the defect benchmark's prior-defects baseline
(``lib/baselines._prior_defect_count`` → ``defect_counter.find_fix_commits`` +
``_attribute``) so the product identifies exactly the commits the benchmark
labels as fixes (product == benchmark):

* resolve the window-start commit = the last commit on/before ``as_of - window``;
* walk the **topological range** ``prior_sha..head`` (every non-merge commit
  merged into HEAD since then — so feature-branch fixes count, matching the
  benchmark's ``prior_sha..t0_sha`` range, not a date-pruned ``--since`` walk);
* classify each commit subject with the shared ``is_fix_commit`` keyword rule;
* attribute a fix to every indexable file it touched.

Reachable-from-HEAD means a T0 worktree never sees post-T0 fixes → leakage-free.

A second pass then opens each matched fix commit's ``-U0`` diff and keeps only
the ones that actually change production code (:mod:`fix_shape`). ``is_fix_commit``
stays byte-identical to the benchmark's regex set; the filter sits after it, and
both totals are returned so the delta is inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from ._constants import PRIOR_DEFECT_WINDOW_DAYS, is_fix_commit
from .fix_shape import classify_fix_shape
from .records import _RECORD_SEP, _extract_rename_paths

logger = structlog.get_logger(__name__)

__all__ = ["PriorDefects", "compute_prior_defects"]

# sha <US> subject, one record per commit (NUL-separated); --name-only then
# emits the touched paths on the lines that follow.
_PRIOR_LOG_FORMAT = "%x00%H%x1f%s"
_FIELD_SEP = "\x1f"

# Fix commits per ``git log --no-walk`` batch in the diff pass. One subprocess
# per batch, so bigger is cheaper — but each sha costs 41 characters of command
# line and Windows caps that at 32 KB, so this stays well clear of the ceiling.
_SHAPE_BATCH_SIZE = 300


@dataclass(frozen=True)
class PriorDefects:
    """Per-file bug-fix counts over the trailing window, before and after filtering.

    *counts* keeps only commits whose diff changes production code and is what
    the ``prior_defect`` biomarker scores. *raw_counts* is every subject-matched
    fix, i.e. what the count was before shape filtering — persisted alongside so
    the noise a repo carries stays visible instead of silently disappearing.
    """

    counts: dict[str, int] = field(default_factory=dict)
    raw_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class _FixCommit:
    """A subject-matched fix commit and the indexable files it touched."""

    sha: str
    paths: list[str]


def compute_prior_defects(
    repo: Any,
    indexable_files: set[str],
    *,
    as_of_ts: float | None,
    window_days: int = PRIOR_DEFECT_WINDOW_DAYS,
) -> PriorDefects:
    """Return per-file bug-fix counts over the trailing window.

    *as_of_ts* anchors the window's end (the repo's HEAD-commit timestamp under
    ``REPOWISE_GIT_WINDOW_ANCHOR``, else wall-clock now). Only commits reachable
    from HEAD are walked, so a historical T0 checkout never sees post-T0 fixes.
    Files outside *indexable_files* are ignored.
    """
    rev_range = _resolve_rev_range(repo, as_of_ts=as_of_ts, window_days=window_days)
    if rev_range is None:
        return PriorDefects()

    fixes = _walk_fix_commits(repo, rev_range, indexable_files)
    if not fixes:
        return PriorDefects()

    raw_counts: dict[str, int] = {}
    for fix in fixes:
        for path in fix.paths:
            raw_counts[path] = raw_counts.get(path, 0) + 1

    shapes = _classify_shapes(repo, [f.sha for f in fixes])
    counts: dict[str, int] = {}
    for fix in fixes:
        # Unknown shape (the diff pass failed) counts as a fix: falling back to
        # the raw behaviour is the safe direction for a defect signal.
        if shapes.get(fix.sha, "code_fix") != "code_fix":
            continue
        for path in fix.paths:
            counts[path] = counts.get(path, 0) + 1

    return PriorDefects(counts=counts, raw_counts=raw_counts)


# ---------------------------------------------------------------------------
# Pass 1: which commits are fixes, and what did they touch
# ---------------------------------------------------------------------------


def _resolve_rev_range(repo: Any, *, as_of_ts: float | None, window_days: int) -> str | None:
    """``prior_sha..head`` for the trailing window, or ``None`` when HEAD is unreadable.

    Falls back to plain ``head`` (every reachable commit) when the repo is
    younger than the window and no boundary commit exists.
    """
    now = datetime.fromtimestamp(as_of_ts, tz=UTC) if as_of_ts is not None else datetime.now(UTC)
    window_start = (now - timedelta(days=window_days)).strftime("%Y-%m-%d")

    try:
        head_sha = repo.head.commit.hexsha
    except Exception as exc:
        logger.debug("prior_defect_head_failed", error=str(exc))
        return None

    # Window-start boundary: the last commit on/before (as_of - window),
    # reachable from HEAD. Mirrors the benchmark's ``resolve_t0_sha``.
    prior_sha = ""
    try:
        prior_sha = repo.git.log(
            head_sha, f"--before={window_start}T23:59:59", "-1", "--format=%H"
        ).strip()
    except Exception as exc:
        logger.debug("prior_defect_window_resolve_failed", error=str(exc))

    return f"{prior_sha}..{head_sha}" if prior_sha else head_sha


def _walk_fix_commits(repo: Any, rev_range: str, indexable_files: set[str]) -> list[_FixCommit]:
    """Subject-matched fix commits in *rev_range*, each with its indexable paths.

    Commits that touched nothing indexable are dropped here so the diff pass
    never pays for them.
    """
    try:
        raw = repo.git.log(
            rev_range,
            "--no-merges",
            "--name-only",
            f"--format={_PRIOR_LOG_FORMAT}",
        )
    except Exception as exc:
        logger.debug("prior_defect_log_failed", error=str(exc))
        return []

    if not raw:
        return []

    fixes: list[_FixCommit] = []
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        header, _, body = record.partition("\n")
        sha, _, subject = header.partition(_FIELD_SEP)
        if not is_fix_commit(subject):
            continue
        paths = [p for p in _record_paths(body) if p in indexable_files]
        if paths:
            fixes.append(_FixCommit(sha=sha, paths=paths))
    return fixes


def _record_paths(body: str) -> list[str]:
    """The ``--name-only`` path lines of one log record, renames resolved."""
    paths: list[str] = []
    for line in body.split("\n"):
        path = line.strip()
        if not path:
            continue
        if "=>" in path:  # rename marker {old => new}
            _old, new = _extract_rename_paths(path, set())
            path = new or path
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# Pass 2: what each fix commit's diff actually changes
# ---------------------------------------------------------------------------


def _classify_shapes(repo: Any, shas: list[str]) -> dict[str, str]:
    """Map each fix sha to its :mod:`fix_shape` kind.

    Batched: one ``git log --no-walk -p`` subprocess per :data:`_SHAPE_BATCH_SIZE`
    commits rather than a ``git show`` spawn each, which is what keeps the pass
    affordable on Windows (where process spawn dominates git's own cost).
    Failures degrade to an empty map, and the caller then counts the commit.
    """
    shapes: dict[str, str] = {}
    for start in range(0, len(shas), _SHAPE_BATCH_SIZE):
        batch = shas[start : start + _SHAPE_BATCH_SIZE]
        try:
            raw = repo.git.log(
                "--no-walk",
                "--patch",
                "--unified=0",
                "--no-color",
                "--format=%x00%H",
                *batch,
            )
        except Exception as exc:
            logger.debug("prior_defect_shape_batch_failed", error=str(exc), commits=len(batch))
            continue
        shapes.update(_parse_shape_batch(raw))
    return shapes


def _parse_shape_batch(raw: str) -> dict[str, str]:
    """Split a batched ``--format=%x00%H --patch`` log into per-sha shape kinds."""
    # Imported here, not at module scope: ``analysis.change_risk`` imports back
    # into this package, so a top-level import would close an import cycle.
    from ...analysis.changed_lines import parse_unified_diff

    shapes: dict[str, str] = {}
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, diff = record.partition("\n")
        sha = sha.strip()
        if len(sha) != 40:
            continue
        shapes[sha] = classify_fix_shape(parse_unified_diff(diff))
    return shapes
