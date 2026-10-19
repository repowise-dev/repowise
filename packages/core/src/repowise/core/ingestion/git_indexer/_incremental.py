"""Stages of :meth:`GitIndexer.index_changed_files` that run after the per-file pass.

The repo-wide walks (prior defects, co-change / entropy) and the idle-file
decay refresh built on them. Each stage logs and swallows its own ``Exception``
so a failed walk leaves the changed rows as the per-file pass wrote them.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from ._constants import _DEFAULT_CO_CHANGE_COMMIT_LIMIT, _MAX_PARTNERS_PER_FILE
from .co_change import CoChangeWalk, compute_co_changes_and_entropy
from .prior_defects import PriorDefects, compute_prior_defects
from .records import _CommitRec

if TYPE_CHECKING:
    from .indexer import GitIndexer

# Logged under the indexer module's name.
logger = structlog.get_logger("repowise.core.ingestion.git_indexer.indexer")


@dataclass(frozen=True)
class SignalPass:
    """What the repo-wide signal stages of one incremental update share."""

    repo: Any
    as_of_ts: float | None
    timings: Any
    # Shuts the private pool and closes the repo when a stage is cancelled.
    abort: Callable[[], None]


@dataclass(frozen=True)
class IdleRefresh:
    """Inputs of the idle-file decay refresh."""

    sink: dict[str, dict] | None
    # Empty when no refresh is due; the sink is set whenever this is not.
    paths: list[str]
    commit_index: dict[str, list[_CommitRec]]
    provenance_classifier: Any
    prior_defects: PriorDefects
    history_files: set[str]


def apply_co_change(rows: list[dict], walk: CoChangeWalk) -> None:
    """Overlay co-change partners and change entropy from *walk* onto *rows*."""
    for meta in rows:
        fp = meta["file_path"]
        if fp in walk.partners:
            meta["co_change_partners_json"] = json.dumps(walk.partners[fp])
        if fp in walk.entropy:
            meta["change_entropy"] = walk.entropy[fp]
        if fp in walk.partner_count:
            meta["co_change_partner_count"] = walk.partner_count[fp]
            meta["co_change_mass"] = walk.partner_mass.get(fp, 0.0)


def apply_prior_defects(rows: list[dict], prior_defects: PriorDefects) -> None:
    """Overlay prior-defect counts onto *rows*."""
    for meta in rows:
        fp = meta["file_path"]
        if fp in prior_defects.counts:
            meta["prior_defect_count"] = prior_defects.counts[fp]
        if fp in prior_defects.raw_counts:
            meta["prior_defect_raw_count"] = prior_defects.raw_counts[fp]


def fallback_note_agents_for(
    repo: Any, fallback_files: set[str], changed_file_paths: list[str]
) -> dict[str, Any]:
    """Git AI note agents, read only when a changed file walks its own history."""
    if not fallback_files.intersection(changed_file_paths):
        return {}
    from ..git_commit_index import load_git_ai_note_agents

    return load_git_ai_note_agents(repo, None)


def idle_refresh_paths(
    commit_index: dict[str, list[_CommitRec]],
    changed: set[str],
    fallback_files: set[str],
) -> list[str]:
    """Files with window commits that are not in this change set."""
    return [
        fp
        for fp, commits in commit_index.items()
        if commits and fp not in changed and fp not in fallback_files
    ]


def prior_defect_stage(
    sp: SignalPass, code_results: list[dict], counted_paths: set[str]
) -> PriorDefects:
    """Recount prior defects over *counted_paths* and overlay them on *code_results*.

    The same windowed pass as the full index; the per-file commit list cannot
    carry this signal accurately on busy repos. A failure yields empty counts.
    """
    from repowise.core.pipeline.phase_timing import timed

    try:
        with timed(sp.timings, "rebuild.git.prior_defects"):
            prior_defects = compute_prior_defects(sp.repo, counted_paths, as_of_ts=sp.as_of_ts)
        apply_prior_defects(code_results, prior_defects)
    except Exception as exc:
        logger.debug("prior_defect_pass_failed", error=str(exc))
        prior_defects = PriorDefects()
    return prior_defects


async def co_change_stage(
    indexer: GitIndexer,
    sp: SignalPass,
    executor: Any,
    walk_files: set[str],
    code_results: list[dict],
    co_change_sink: dict[str, list[dict]] | None,
    idle: IdleRefresh,
) -> None:
    """Rerun the co-change / entropy walk, then the idle decay refresh for ``idle.paths``.

    ``index_file`` resets both fields to empty defaults and the upsert writes
    rows field by field, so without this walk an update would blank the
    partners of every file it touched.
    """
    from repowise.core.pipeline.phase_timing import timed

    try:
        with timed(sp.timings, "rebuild.git.co_change"):
            walk = await asyncio.get_event_loop().run_in_executor(
                executor,
                compute_co_changes_and_entropy,
                sp.repo,
                walk_files,
                max(indexer.commit_limit, _DEFAULT_CO_CHANGE_COMMIT_LIMIT),
                _MAX_PARTNERS_PER_FILE,
                None,
                None,
                sp.as_of_ts,
            )
        apply_co_change(code_results, walk)
        if co_change_sink is not None:
            co_change_sink.update(walk.partners)
        if idle.paths:
            await _idle_decay_stage(indexer, sp, idle, walk)
    except Exception as exc:
        logger.debug("co_change_pass_failed", error=str(exc))
    except BaseException:
        sp.abort()
        raise


async def _idle_decay_stage(
    indexer: GitIndexer, sp: SignalPass, idle: IdleRefresh, walk: CoChangeWalk
) -> None:
    """Recompute only the anchor-dependent window / decay fields of idle files.

    Reuses the walks already loaded: ``index_file`` with precomputed commits
    and blame off runs no git subprocess and no file I/O.
    """
    from repowise.core.pipeline.phase_timing import timed

    assert idle.sink is not None
    with timed(sp.timings, "rebuild.git.idle_decay"):
        idle.sink.update(
            await asyncio.to_thread(
                indexer._compute_idle_decay,
                sp.repo,
                idle.paths,
                idle.commit_index,
                sp.as_of_ts,
                idle.provenance_classifier,
                walk,
                idle.prior_defects,
                idle.history_files,
            )
        )
