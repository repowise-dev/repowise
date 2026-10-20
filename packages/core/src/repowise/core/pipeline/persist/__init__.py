"""Shared persistence logic for pipeline results.

Extracted from ``cli/commands/init_cmd.py`` so both the CLI and the server
can persist a ``PipelineResult`` without duplicating the upsert recipe.
"""

# The steps live in the submodules; every name they define, private ones
# included, is re-exported here so imports and patches on this module keep working.
# A submodule calls a step that callers patch here through the package
# (``_persist.<name>``), never by bare name, or the patch stops reaching it.
# ruff: noqa: F401

from __future__ import annotations

import dataclasses
from typing import Any

import structlog

from .analysis import (
    persist_analysis,
)
from .decisions import (
    apply_decision_lifecycle,
    repair_decision_store,
    store_decisions,
)
from .generation import (
    persist_generation,
    persist_kg,
)
from .git import (
    _rebuild_changes,
    persist_git,
    persist_git_refresh,
    replace_git_history,
)
from .graph import (
    _betweenness_commits,
    _changed_file_edges,
    _changed_file_symbols,
    _community_meta,
    _derive_entry_point_scores,
    _edge_row,
    _edges_predate_cohesion,
    _prune_vanished_symbol_nodes,
    _stamp_betweenness_commit,
    _stored_edges_parser_fingerprint,
    has_graph_edges,
    persist_graph_nodes,
    persist_incremental_edges,
    persist_incremental_symbols,
    persist_symbol_analysis,
    stamp_edges_parser_fingerprint,
)
from .health import (
    _analyzed_commit,
    refresh_governance_findings,
    save_full_health_report,
    snapshot_health_from_store,
)
from .ingestion import (
    persist_ingestion,
)
from .prune import (
    _PRUNE_CHUNK,
    _PRUNE_FLOOR_MIN_ROWS,
    _PRUNE_MAX_FRACTION,
    _FileLiveness,
    _git_tracked_paths,
    _prune_stale_file_rows,
    _resolve_plans_of_deleted_files,
    path_liveness,
    prune_deleted_file_rows,
)
from .security import (
    _replace_security_findings,
    persist_incremental_security_findings,
    persist_security_findings,
)
from .sweeps import (
    _SWEPT_GENERATED_PAGE_TYPES,
    _sweep_stale_generated_pages,
    _symbol_names_by_file,
    sweep_absent_cycle_pages,
    sweep_absent_symbol_spotlights,
    sweep_deselected_pages,
    sweep_retired_pages,
    sweep_superseded_generated_pages,
)
from .tombstones import (
    _SPOTLIGHT_PATHS_PER_QUERY,
    _STALE_ID_CHUNK,
    _log,
    mark_page_ids_stale,
    mark_stale_pages,
    mark_tombstone_pages,
    reconcile_full_index_scope,
    tombstone_absent_file_pages,
    tombstone_candidates,
    tombstone_file_pages_outside_scope,
    tombstone_pages_outside_generation,
)

logger = structlog.get_logger(__name__)


async def _refresh_commit_health(result: Any, session: Any, repo_id: str) -> None:
    """Seed the per-commit health rows for the commits this run wrote."""
    repo_path = getattr(result, "repo_path", "")
    summary = getattr(result, "git_summary", None)
    if not repo_path or summary is None:
        return
    from ..commit_health import recent_shas, refresh_commit_health

    await refresh_commit_health(
        session,
        repo_id,
        repo_path,
        recent_shas(getattr(summary, "commit_rows", None)),
    )


async def persist_pipeline_result(
    result: Any,
    session: Any,
    repo_id: str,
    *,
    replace_full_git_history: bool = False,
) -> list[str]:
    """Persist all outputs from a :class:`PipelineResult` into the database.

    Thin composition of the four phase-scoped persisters
    (:func:`persist_ingestion`, :func:`persist_git`, :func:`persist_analysis`,
    :func:`persist_generation`) in dependency order. The same functions are
    reused by the incremental-persistence path so a resumed run can persist
    one phase at a time.

    Parameters
    ----------
    result:
        A ``PipelineResult`` from ``run_pipeline()``.
    session:
        An active SQLAlchemy ``AsyncSession`` (caller manages commit/rollback).
    repo_id:
        The repository ID to associate all records with.

    Returns
    -------
    The page ids of stale generated pages swept by this run. Callers should
    remove them from the FTS index after the session closes.

    Note
    ----
    FTS indexing is intentionally excluded here — callers must do it after
    this session closes to avoid SQLite write-lock conflicts.

    This function mutates ``sym.file_path`` on parsed-file symbols that
    lack one.  Callers should treat *result* as consumed after this call.
    """
    # Prune rows for files absent from this full result before the phase
    # persisters re-upsert. graph/analysis tables key off parsed_files;
    # git_metadata keys off the git indexer's set (a file can be git-tracked
    # but unparsed). Runs only here, never in the reusable phase persisters.
    # ``file_infos`` is the successful traversal set and survives individual
    # read/parse failures. Using parsed_files here would misclassify a locked
    # or temporarily unparsable file as excluded and erase its prior rows.
    current_graph_file_paths = {file_info.path for file_info in result.file_infos}
    current_git_file_paths = {
        (gm if isinstance(gm, dict) else dataclasses.asdict(gm)).get("file_path", "")
        for gm in result.git_metadata_list
    }
    current_git_file_paths.discard("")
    swept_page_ids = await reconcile_full_index_scope(
        session, repo_id, current_graph_file_paths, current_git_file_paths
    )

    symbol_count = await persist_ingestion(result, session, repo_id)
    if replace_full_git_history:
        from repowise.core.persistence.crud import purge_proposed_decisions_by_source

        await purge_proposed_decisions_by_source(session, repo_id, "git_archaeology")
        await replace_git_history(
            session,
            repo_id,
            {
                (gm if isinstance(gm, dict) else dataclasses.asdict(gm))["file_path"]: (
                    gm if isinstance(gm, dict) else dataclasses.asdict(gm)
                )
                for gm in result.git_metadata_list
            },
            result.git_summary,
        )
    else:
        await persist_git(result, session, repo_id)
    await persist_analysis(result, session, repo_id)
    await persist_generation(result, session, repo_id)

    # What each recent commit did to health. Needs the working tree, so it
    # runs here rather than on the read path, and it is bounded: older commits
    # keep no row until a later update reaches them.
    await _refresh_commit_health(result, session, repo_id)

    # Sweep structurally-keyed generated pages (module/layer/scc) that this
    # run did not reproduce — their ids drift between runs, so without the
    # sweep every re-index strands the previous set as duplicates. Full runs
    # only, same rule as _prune_stale_file_rows.
    swept_page_ids += await _sweep_stale_generated_pages(
        session,
        repo_id,
        result.generated_pages,
        getattr(result, "authoritative_page_types", None),
        getattr(result, "preserved_page_ids", None),
    )
    # Rows of a page type that no longer exists at all. Independent of what
    # this run produced, so it runs on every path rather than only here.
    swept_page_ids += await sweep_retired_pages(session, repo_id)

    # Placement depends on the whole page set, so it is computed here rather
    # than during generation, after the sweep has retired anything stale.
    from ..page_tree_sync import rebuild_page_tree

    await rebuild_page_tree(session, repo_id)

    logger.info(
        "pipeline_result_persisted",
        repo_id=repo_id,
        pages=len(result.generated_pages) if result.generated_pages else 0,
        graph_nodes=result.graph_builder.graph().number_of_nodes(),
        symbols=symbol_count,
        git_files=len(result.git_metadata_list),
    )
    return swept_page_ids
