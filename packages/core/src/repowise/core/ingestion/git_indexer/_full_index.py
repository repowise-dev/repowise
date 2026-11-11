"""Stages of :meth:`GitIndexer.index_repo` that run after the parallel walks."""

from __future__ import annotations

import json
from typing import Any, NamedTuple

import structlog

from ._incremental import apply_co_change, apply_prior_defects
from .co_change import CoChangeWalk
from .enrich import compute_percentiles
from .prior_defects import PriorDefects
from .records import GitHistoryCoverage, _CommitRec

# Logged under the indexer module's name.
logger = structlog.get_logger("repowise.core.ingestion.git_indexer.indexer")


class CommitSample(NamedTuple):
    """The repo-wide commit-index walk of a full index, or its rename-tracking stand-in."""

    commits: dict[str, list[_CommitRec]]
    fallback_files: set[str]
    recent_files: set[str]
    deep_files: set[str]
    global_commits: int
    deep_commits: int
    complete_through_depth: int


def merge_code_signals(
    code_results: list[dict],
    walk: CoChangeWalk,
    prior_defects: PriorDefects,
    trace_index: Any,
) -> None:
    """Overlay co-change, entropy, prior defects and AI line share, then percentiles.

    The line share is keyed by path, so it merges whichever commit-index walk
    (if any) ran.
    """
    trace_line_shares = trace_index.line_shares() if trace_index else {}
    apply_co_change(code_results, walk)
    apply_prior_defects(code_results, prior_defects)
    for meta in code_results:
        share = trace_line_shares.get(meta["file_path"])
        if share:
            meta["agent_line_count"] = share[0]
            meta["agent_line_model_json"] = json.dumps(share[1])

    compute_percentiles(code_results)


def build_commit_tables(
    commit_sink: list[dict], path_prefix: str = ""
) -> tuple[list[dict], list[dict]]:
    """Per-commit rows and their file detail from the commit-index walk's parsed diffs.

    Empty in rename-tracking mode (no batched commit index). Failure-isolated
    so a change_risk hiccup never breaks file-level git metadata. *path_prefix*
    narrows the per-file detail (not the commit rows) to one directory.
    """
    if not commit_sink:
        return [], []
    try:
        from .commit_rows import build_commit_file_rows, build_commit_rows

        built = build_commit_rows(commit_sink)
        built_files = build_commit_file_rows(commit_sink, path_prefix)
    except Exception as exc:
        logger.debug("commit_rows_build_failed", error=str(exc))
        return [], []
    # Returned together: a half-built pair would write commits whose file
    # detail describes a different set.
    return built, built_files


def history_coverage(
    indexable_files: list[str],
    code_results: list[dict],
    sample: CommitSample,
    per_file_limit: int,
    workers: int,
) -> GitHistoryCoverage:
    """How much history the full index saw; it describes the code tier."""
    return GitHistoryCoverage(
        eligible_files=len(indexable_files),
        files_with_history=sum(
            1 for row in code_results if int(row.get("commit_count_total", 0)) > 0
        ),
        unavailable_files=sum(1 for row in code_results if "commit_count_total" not in row),
        retained_commits=sum(int(row.get("commit_count_total", 0)) for row in code_results),
        per_file_limit=per_file_limit,
        global_commits=sample.global_commits,
        deep_commits=sample.deep_commits,
        recent_files=len(sample.recent_files.intersection(indexable_files)),
        deep_files=len(sample.deep_files.intersection(indexable_files)),
        fallback_files=len(sample.fallback_files.intersection(indexable_files)),
        complete_through_depth=sample.complete_through_depth,
        workers=workers,
    )
