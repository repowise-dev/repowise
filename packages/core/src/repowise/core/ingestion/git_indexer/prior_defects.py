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
from typing import TYPE_CHECKING, Any

import structlog

from ._constants import PRIOR_DEFECT_WINDOW_DAYS, is_fix_commit
from .fix_shape import classify_fix_shape
from .records import _RECORD_SEP, _extract_rename_paths

if TYPE_CHECKING:
    # Type-only: ``analysis.change_risk`` imports back into this package, so a
    # runtime import would close an import cycle.
    from ...analysis.changed_lines import FileDiff

logger = structlog.get_logger(__name__)

__all__ = ["FixCommit", "FixWalk", "PriorDefects", "collect_fix_commits", "compute_prior_defects"]

# sha <US> committer-time <US> subject, one record per commit (NUL-separated);
# --name-only then emits the touched paths on the lines that follow.
_PRIOR_LOG_FORMAT = "%x00%H%x1f%ct%x1f%s"
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
class FixCommit:
    """A subject-matched fix commit: what it touched and what its diff looks like.

    *paths* is the indexable subset of the files it changed. *files* is its
    parsed ``-U0`` diff keyed by path — the old-side ranges and changed line text
    the shape classifier reads and SZZ blames. *shape_kind* falls back to
    ``code_fix`` when the diff pass failed for this commit, which keeps the count
    on the pre-filter (safe) side of the change.
    """

    sha: str
    ts: int
    paths: list[str]
    shape_kind: str = "code_fix"
    files: dict[str, FileDiff] = field(default_factory=dict)


@dataclass(frozen=True)
class FixWalk:
    """Every fix commit in the trailing window, plus the walk's own trailing edge.

    *oldest_fix_ts* is the committer time of the oldest fix the walk found, taken
    BEFORE any ``skip_shas`` narrowing (unix seconds; 0 when the walk found
    nothing). Callers that persist per-commit rows prune below it, so what they
    keep is exactly what the walk contains.

    Deriving the cutoff from the walk rather than from the window's date matters,
    and both halves of that are load-bearing. The rev range is
    ``prior_sha..head``, which is reachability, not time: a merged long-lived
    branch contributes commits older than the date boundary, and a date-based
    prune would delete them immediately after every index inserted them, then
    re-blame them on the next update, forever. And ``git log --before`` reads its
    argument in local time while a Python timestamp is UTC, so the two cutoffs
    would not even agree on the same instant.
    """

    fixes: list[FixCommit] = field(default_factory=list)
    oldest_fix_ts: int = 0


def collect_fix_commits(
    repo: Any,
    indexable_files: set[str],
    *,
    as_of_ts: float | None,
    window_days: int = PRIOR_DEFECT_WINDOW_DAYS,
    skip_shas: set[str] | None = None,
) -> FixWalk:
    """Walk the trailing window's fix commits and open each one's diff.

    The shared front half of the defect pass: :func:`compute_prior_defects`
    counts these, and :mod:`fix_events` turns them into per-file rows and blames
    them. One walk, one batched diff pass, two consumers.

    *skip_shas* drops commits before the diff pass, which is what keeps an update
    cheap: the name-only walk is a single ``git log``, while opening 200 patches
    is the part that costs real time, and an update with no new fix commits
    should not pay for it. Counting callers must not pass it - a skipped commit
    is missing from the walk entirely, not merely un-diffed.

    Ceiling: the returned walk holds every fix commit's parsed patch, changed
    line text included, for as long as the caller keeps it. Bounded in practice
    by the window (a few hundred commits), but a repo whose fixes routinely touch
    multi-megabyte generated files will hold all of that at once. If that ever
    bites, drop ``removed``/``added`` after classification and carry the LOC
    counts instead - the shape classifier is the only thing that needs the text
    for non-code paths.
    """
    rev_range = _resolve_rev_range(repo, as_of_ts=as_of_ts, window_days=window_days)
    if rev_range is None:
        return FixWalk()

    fixes = _walk_fix_commits(repo, rev_range, indexable_files)
    # Taken before the skip, so an update and a full index at the same HEAD agree
    # on the trailing edge even though they trace different commits.
    oldest = min((f.ts for f in fixes if f.ts > 0), default=0)
    if skip_shas:
        fixes = [f for f in fixes if f.sha not in skip_shas]
    if not fixes:
        return FixWalk(oldest_fix_ts=oldest)

    diffs = _parse_fix_diffs(repo, [f.sha for f in fixes])
    resolved_fixes = [
        FixCommit(
            sha=fix.sha,
            ts=fix.ts,
            paths=fix.paths,
            shape_kind=classify_fix_shape(diffs[fix.sha]) if fix.sha in diffs else "code_fix",
            files=diffs.get(fix.sha, {}),
        )
        for fix in fixes
    ]
    return FixWalk(fixes=resolved_fixes, oldest_fix_ts=oldest)


def compute_prior_defects(
    repo: Any,
    indexable_files: set[str],
    *,
    as_of_ts: float | None,
    window_days: int = PRIOR_DEFECT_WINDOW_DAYS,
    walk: FixWalk | None = None,
) -> PriorDefects:
    """Return per-file bug-fix counts over the trailing window.

    *as_of_ts* anchors the window's end (the repo's HEAD-commit timestamp under
    ``REPOWISE_GIT_WINDOW_ANCHOR``, else wall-clock now). Only commits reachable
    from HEAD are walked, so a historical T0 checkout never sees post-T0 fixes.
    Files outside *indexable_files* are ignored.

    *walk* lets a caller that already ran :func:`collect_fix_commits` reuse it
    instead of paying for a second walk and diff pass.
    """
    if walk is None:
        walk = collect_fix_commits(
            repo, indexable_files, as_of_ts=as_of_ts, window_days=window_days
        )
    if not walk.fixes:
        return PriorDefects()

    raw_counts: dict[str, int] = {}
    counts: dict[str, int] = {}
    for fix in walk.fixes:
        for path in fix.paths:
            raw_counts[path] = raw_counts.get(path, 0) + 1
        if fix.shape_kind != "code_fix":
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


def _walk_fix_commits(repo: Any, rev_range: str, indexable_files: set[str]) -> list[FixCommit]:
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

    fixes: list[FixCommit] = []
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        header, _, body = record.partition("\n")
        sha, _, rest = header.partition(_FIELD_SEP)
        committed, _, subject = rest.partition(_FIELD_SEP)
        if not is_fix_commit(subject):
            continue
        paths = [p for p in _record_paths(body) if p in indexable_files]
        if paths:
            fixes.append(FixCommit(sha=sha, ts=_as_ts(committed), paths=paths))
    return fixes


def _as_ts(raw: str) -> int:
    try:
        return int(raw.strip())
    except ValueError:
        return 0


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


def _parse_fix_diffs(repo: Any, shas: list[str]) -> dict[str, dict[str, FileDiff]]:
    """Map each fix sha to its parsed ``-U0`` diff, keyed by file path.

    Batched: one ``git log --no-walk -p`` subprocess per :data:`_SHAPE_BATCH_SIZE`
    commits rather than a ``git show`` spawn each, which is what keeps the pass
    affordable on Windows (where process spawn dominates git's own cost).
    Failures degrade to a missing entry, and the caller then treats the commit as
    a code fix.
    """
    diffs: dict[str, dict[str, FileDiff]] = {}
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
        diffs.update(_parse_diff_batch(raw))
    return diffs


def _parse_diff_batch(raw: str) -> dict[str, dict[str, FileDiff]]:
    """Split a batched ``--format=%x00%H --patch`` log into per-sha parsed diffs."""
    # Imported here, not at module scope: ``analysis.change_risk`` imports back
    # into this package, so a top-level import would close an import cycle.
    from ...analysis.changed_lines import parse_unified_diff

    diffs: dict[str, dict[str, FileDiff]] = {}
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, diff = record.partition("\n")
        sha = sha.strip()
        if len(sha) != 40:
            continue
        diffs[sha] = parse_unified_diff(diff)
    return diffs
