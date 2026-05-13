"""Single-pass repo-wide commit index for git_indexer.

The original per-file path in ``git_indexer._index_file`` spawned one
``git log --numstat`` subprocess per tracked file. On a 5,000-file repo
that meant 5,000 process spawns — ~50–100 ms each on Windows — which
made the git phase dominate the total ``repowise init`` wall-clock.

This module replaces the fan-out with one repo-wide ``git log`` pass
and an in-memory bucketing step. The shape mirrors what
``_compute_co_changes`` already does — one subprocess, fan-out via
Python dicts — so any future debugging only has one log format to
understand.

The batched path is only used when ``follow_renames=False`` (the
default). Rename-tracking still falls back to the per-file ``--follow``
path because git's rename heuristics are evaluated against a single
input file, not retro-fittable from a repo-wide log.
"""

from __future__ import annotations

import structlog
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .git_indexer import _CommitRec

logger = structlog.get_logger(__name__)

# Git log record separator (NUL byte) and field separator (US, 0x1f) —
# chosen so they can't appear in any commit metadata. Subjects can
# legally contain anything else, so we cannot use printable separators.
_RECORD_SEP = "\x00"
_FIELD_SEP = "\x1f"

_LOG_FORMAT = f"{_RECORD_SEP}%H{_FIELD_SEP}%an{_FIELD_SEP}%ae{_FIELD_SEP}%ct{_FIELD_SEP}%P{_FIELD_SEP}%s"


def load_commit_index(
    repo: object,
    commit_limit: int,
    indexable_files: set[str],
) -> dict[str, list["_CommitRec"]]:
    """Bucket every commit in the recent history by the files it touched.

    *commit_limit* caps the depth (newest first); *indexable_files* is
    the allowlist of paths the caller will later read. Files outside
    this set are silently dropped — they are still seen by co-change
    detection upstream, but per-file metadata is only produced for the
    indexable set so there is no benefit to retaining their commits
    here.

    Returns a dict mapping ``file_path → [commit records, newest first]``.
    Files with no commits in the window simply aren't present in the
    dict; callers should treat ``KeyError`` / ``get(file, [])`` as
    "no recorded history" rather than an error.

    Failures (git unavailable, corrupt log output, etc.) return an
    empty dict so the caller can fall back to per-file indexing.
    """
    # Imported here to avoid a circular import — _CommitRec lives in
    # git_indexer and this module is imported from there.
    from .git_indexer import _CommitRec, _extract_rename_paths

    try:
        raw = repo.git.log(  # type: ignore[attr-defined]
            f"-{commit_limit}",
            "--numstat",
            "--no-merges",
            f"--format={_LOG_FORMAT}",
        )
    except Exception as exc:
        logger.warning("repo_commit_index_failed", error=str(exc))
        return {}

    if not raw:
        return {}

    bucket: dict[str, list[_CommitRec]] = {}
    current_meta: tuple[str, str, str, int, bool, str] | None = None

    for line in raw.splitlines():
        if line.startswith(_RECORD_SEP):
            parts = line.lstrip(_RECORD_SEP).split(_FIELD_SEP)
            if len(parts) < 6:
                current_meta = None
                continue
            sha, an, ae, ct, parents, subj = parts[:6]
            try:
                ts = int(ct)
            except ValueError:
                ts = 0
            is_merge = len(parents.split()) > 1
            current_meta = (sha, an or "unknown", ae, ts, is_merge, subj)
            continue

        if current_meta is None or not line.strip():
            continue

        # numstat line: added\tdeleted\tpath
        cols = line.split("\t")
        if len(cols) < 3:
            continue

        stat_path = cols[2]
        # Handle rename markers — ``{old => new}`` resolves to a new
        # path. Without ``--follow`` git still emits these for moves
        # detected via the rename heuristic; we add both names but
        # attribute the churn to the new path.
        if "=>" in stat_path:
            seen: set[str] = set()
            old_path, new_path = _extract_rename_paths(stat_path, seen)
            target = new_path or stat_path
        else:
            target = stat_path

        if target not in indexable_files:
            continue

        try:
            added = int(cols[0]) if cols[0] != "-" else 0
            deleted = int(cols[1]) if cols[1] != "-" else 0
        except ValueError:
            added = 0
            deleted = 0

        sha, an, ae, ts, is_merge, subj = current_meta
        # Each commit becomes one record per file it touched — the
        # per-file analyzer treats this list as the file's own history.
        bucket.setdefault(target, []).append(
            _CommitRec(
                sha=sha,
                author_name=an,
                author_email=ae,
                ts=ts,
                is_merge=is_merge,
                subject=subj,
                added=added,
                deleted=deleted,
            )
        )

    logger.debug(
        "repo_commit_index_built",
        commits_parsed=raw.count(_RECORD_SEP),
        files_with_history=len(bucket),
        indexable_files=len(indexable_files),
    )
    return bucket
