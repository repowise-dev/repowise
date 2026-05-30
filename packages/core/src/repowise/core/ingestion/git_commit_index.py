"""Single-pass repo-wide commit index for git_indexer.

The original per-file path in ``git_indexer._index_file`` spawned one
``git log --numstat`` subprocess per tracked file. On a 5,000-file repo
that meant 5,000 process spawns — ~50-100 ms each on Windows — which
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

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .git_indexer import _CommitRec

logger = structlog.get_logger(__name__)


def load_commit_index(
    repo: object,
    commit_limit: int,
    indexable_files: set[str],
    *,
    commit_sink: list[dict] | None = None,
) -> dict[str, list[_CommitRec]]:
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

    When *commit_sink* is supplied, each parsed commit is appended to it as a
    raw dict (``sha``, ``author_name``, ``author_email``, ``ts``, ``subject``,
    and ``changes`` — the full ``(path, added, deleted)`` list across *all*
    files in the commit, not just the indexable subset, so change diffusion is
    measured against the real footprint). This rides the same single walk —
    no extra git pass — and lets the caller build per-commit rows downstream
    (see :mod:`git_indexer.commit_rows`). The default (``None``) leaves the
    return value and behaviour unchanged.

    Failures (git unavailable, corrupt log output, etc.) return an
    empty dict so the caller can fall back to per-file indexing.
    """
    # Imported here to avoid a circular import — these live in git_indexer's
    # records module and this module is imported from there.
    from .git_indexer import (
        _LOG_FORMAT,
        _RECORD_SEP,
        _CommitRec,
        _extract_rename_paths,
        _parse_commit_record,
    )

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
    commits_parsed = 0

    # Split on the NUL record separator rather than newlines: commit bodies
    # (``%b``) are multi-line, so a line-based scan would mistake body lines
    # for numstat rows. The first chunk before the leading separator is empty.
    for record in raw.split(_RECORD_SEP):
        if not record.strip():
            continue
        parsed = _parse_commit_record(record)
        if parsed is None:
            continue
        header, numstat_lines = parsed
        commits_parsed += 1

        # Full change footprint of this commit (every file, not just the
        # indexable subset) — only accumulated when a sink is requested so the
        # default path pays nothing.
        commit_changes: list[tuple[str, int, int]] = [] if commit_sink is not None else None  # type: ignore[assignment]

        for line in numstat_lines:
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
                _old_path, new_path = _extract_rename_paths(stat_path, seen)
                target = new_path or stat_path
            else:
                target = stat_path

            try:
                added = int(cols[0]) if cols[0] != "-" else 0
                deleted = int(cols[1]) if cols[1] != "-" else 0
            except ValueError:
                added = 0
                deleted = 0

            if commit_changes is not None:
                commit_changes.append((target, added, deleted))

            if target not in indexable_files:
                continue

            # Each commit becomes one record per file it touched — the
            # per-file analyzer treats this list as the file's own history.
            bucket.setdefault(target, []).append(
                _CommitRec(
                    sha=header["sha"],
                    author_name=header["author_name"],
                    author_email=header["author_email"],
                    ts=header["ts"],
                    is_merge=header["is_merge"],
                    subject=header["subject"],
                    body=header["body"],
                    added=added,
                    deleted=deleted,
                )
            )

        if commit_sink is not None:
            commit_sink.append(
                {
                    "sha": header["sha"],
                    "author_name": header["author_name"],
                    "author_email": header["author_email"],
                    "ts": header["ts"],
                    "subject": header["subject"],
                    "changes": commit_changes,
                }
            )

    logger.debug(
        "repo_commit_index_built",
        commits_parsed=commits_parsed,
        files_with_history=len(bucket),
        indexable_files=len(indexable_files),
    )
    return bucket
