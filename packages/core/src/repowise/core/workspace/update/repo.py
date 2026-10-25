"""Single-repo index refresh: the incremental path with a full-pipeline fallback."""

from __future__ import annotations

import json as _json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from ...ingestion.git_indexer import git_history_stale
from ...ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT
from .state import commit_exists, get_head_commit, read_repo_state

_log = logging.getLogger("repowise.workspace.update")


async def _merged_repo_excludes(
    repo_path: Path,
    extra_exclude_patterns: list[str] | None = None,
) -> list[str]:
    """config.yaml excludes, else a legacy settings copy, plus *extra_exclude_patterns*.

    The legacy copy (``settings_json`` from before the web UI wrote
    config.yaml) applies only while the file lacks the key. Local indexes read
    it straight from the repo-local ``wiki.db`` (cheaper than an engine for one
    row); a configured shared database is the source of truth instead.
    """
    from ...persistence.database import get_configured_db_url
    from ...repo_config import resolve_file_settings

    configured_url = get_configured_db_url()
    if configured_url is None:
        settings_json = _local_settings_json(repo_path)
    else:
        settings_json = await _configured_settings_json(configured_url, repo_path)

    stored = _settings_excludes(settings_json) if settings_json else {}
    saved = resolve_file_settings(repo_path, stored).get("exclude_patterns") or []
    merged = [p for p in saved if isinstance(p, str)] + list(extra_exclude_patterns or [])
    return list(dict.fromkeys(merged))


def _local_settings_json(repo_path: Path) -> str | None:
    """``settings_json`` from the repo-local ``wiki.db``, or ``None``."""
    db_path = repo_path / ".repowise" / "wiki.db"
    if not db_path.is_file():
        return None
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute("SELECT settings_json FROM repositories LIMIT 1").fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        _log.debug("Could not read repo settings from local wiki.db", exc_info=True)
    return None


async def _configured_settings_json(configured_url: str, repo_path: Path) -> str | None:
    """``settings_json`` of this repo's row in the configured database, or ``None``."""
    from ...persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from ...persistence.crud import get_repository_by_path

    settings_json: str | None = None
    try:
        engine = create_engine(configured_url)
        try:
            await init_db(engine)
            sf = create_session_factory(engine)
            async with get_session(sf) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is not None and repo.settings_json:
                    settings_json = repo.settings_json
        finally:
            await engine.dispose()
    except Exception:
        _log.warning(
            "Could not read repo settings from the shared database; "
            "saved exclude patterns not applied",
            exc_info=True,
        )
    return settings_json


def _settings_excludes(settings_json: str) -> dict[str, Any]:
    """``{"exclude_patterns": ...}`` from *settings_json* when it has them, else ``{}``."""
    try:
        settings = _json.loads(settings_json)
    except Exception:
        _log.warning(
            "Could not parse settings_json; saved exclude patterns not applied", exc_info=True
        )
        return {}
    if isinstance(settings, dict) and "exclude_patterns" in settings:
        return {"exclude_patterns": settings["exclude_patterns"]}
    return {}


@dataclass
class RepoUpdateResult:
    """Result of updating a single repo within a workspace."""

    alias: str
    updated: bool  # True if an update was performed
    skipped_reason: str | None = None  # "up_to_date", "missing_directory", etc.
    # How long the winning update had held the lock, when this repo deferred to
    # one. Carried on the result because the caller reports the deferral and
    # the age is the part that tells a slow update apart from a wedged one.
    lock_age_seconds: float | None = None
    file_count: int = 0
    symbol_count: int = 0
    error: str | None = None
    first_time_indexed: bool = False  # True if this run was a first-time index
    # state.json "knowledge_graph" summary block when this run refreshed the
    # KG; None means the persisted block is still current. State-file writes
    # stay with the caller, so the block rides on the result.
    kg_state: dict[str, Any] | None = None
    # Repo-relative paths this run indexed from the working tree rather than
    # from a commit. Persisted as state.json "working_tree_paths" so the next
    # run can tell which of them have since been reverted or deleted — working
    # tree state has no ``last_sync_commit`` to diff against. None on the
    # commit-anchored path, which leaves the stored list alone.
    working_tree_paths: list[str] | None = None
    phase_timings: dict[str, float] | None = None
    # True when this run walked the whole git history and persisted every row,
    # so the caller stamps ``git_history_version``.
    git_history_refreshed: bool = False


async def _incremental_repo_update(
    repo_path: Path,
    *,
    state: dict[str, Any],
    base_ref: str,
    exclude_patterns: list[str] | None = None,
    include_working_tree: bool = False,
    force_full_git: bool = False,
) -> RepoUpdateResult | None:
    """Refresh an already-indexed repo through the incremental update path.

    Mirrors the single-repo ``repowise update --index-only`` flow: diff
    ``base_ref..HEAD``, rebuild the graph (parse-cache backed), re-index git
    metadata for the changed files only, run partial health/dead-code
    analysis, and upsert the results. Deletions and renames prune stale rows
    and tombstone pages, as the single-repo path does. State-file updates stay
    with the caller.

    Raises on failure — the caller falls back to the full pipeline.
    """
    from ...pipeline.incremental import persist_incremental_index, rebuild_graph_and_git
    from ...pipeline.phases.git import drop_transient_git_signals

    alias = repo_path.name
    file_diffs, working_tree_paths = _changed_file_diffs(
        repo_path, state, base_ref, include_working_tree
    )
    if not file_diffs and not force_full_git:
        # Nothing the index cares about changed: report success so the caller
        # bumps ``last_sync_commit`` instead of re-diffing forever.
        return RepoUpdateResult(alias=alias, updated=True, working_tree_paths=working_tree_paths)

    # Per-repo config; workspace-level ``exclude_patterns`` apply on top.
    from ...repo_config import load_repo_config

    cfg = load_repo_config(repo_path)
    merged_excludes = await _merged_repo_excludes(repo_path, exclude_patterns)

    # Decay-only rows for idle files, persisted beside the changed rows but kept
    # out of git_meta_map so partial health's repo-wide aggregates are unaffected.
    git_decay_map: dict[str, dict] = {}
    full_git_summaries: list[Any] = []
    (
        parsed_files,
        source_map,
        graph_builder,
        _structure,
        file_count,
        git_meta_map,
    ) = await rebuild_graph_and_git(
        repo_path,
        file_diffs,
        cfg,
        merged_excludes,
        git_tier=state.get("git_tier"),
        include_submodules=bool(state.get("include_submodules", False)),
        include_nested_repos=bool(state.get("include_nested_repos", False)),
        idle_decay_sink=git_decay_map,
        force_full_git=force_full_git,
        git_summary_sink=full_git_summaries,
        log=_log.info,
    )

    partial_health_report, dead_code_report, doc_drift_report = await _partial_analysis(
        repo_path, base_ref, file_diffs, parsed_files, source_map, graph_builder, git_meta_map
    )

    # Drop the transient, non-serializable ``BlameIndex`` before persistence.
    drop_transient_git_signals(list(git_meta_map.values()))

    # Refresh the knowledge graph (layers/tour/entry points) when the graph shape changed.
    from ...pipeline.incremental import refresh_knowledge_graph

    kg = await refresh_knowledge_graph(
        repo_path,
        parsed_files,
        graph_builder,
        _structure,
        git_meta_map,
        dead_code_report,
        prior_fingerprint=(state.get("knowledge_graph") or {}).get("fingerprint"),
        log=_log.info,
    )

    # A full walk is stamped only when its rows reached the store.
    failed_steps: list[str] = []
    await persist_incremental_index(
        repo_path,
        graph_builder,
        git_meta_map,
        dead_code_report,
        partial_health_report,
        [fd.path for fd in file_diffs],
        doc_drift_report=doc_drift_report,
        current_graph_file_paths={pf.file_info.path for pf in parsed_files},
        # Tombstones pages for deleted/renamed paths.
        file_diffs=file_diffs,
        knowledge_graph_result=kg,
        parsed_files=parsed_files,
        source_map=source_map,
        git_decay_map=git_decay_map,
        full_git_summary=full_git_summaries[0] if full_git_summaries else None,
        failed_steps=failed_steps,
        log=_log.info,
    )

    return RepoUpdateResult(
        alias=alias,
        updated=True,
        file_count=file_count,
        symbol_count=sum(pf.symbol_count for pf in parsed_files),
        kg_state=_export_kg_state(repo_path, alias, kg),
        working_tree_paths=working_tree_paths,
        git_history_refreshed=bool(full_git_summaries) and "Git persist" not in failed_steps,
    )


def _changed_file_diffs(
    repo_path: Path, state: dict[str, Any], base_ref: str, include_working_tree: bool
) -> tuple[list, list[str] | None]:
    """The ``base_ref..HEAD`` diffs, plus working-tree ones when asked.

    Returns ``(file_diffs, working_tree_paths)``; the paths are ``None`` on the
    commit-anchored path.
    """
    from ...ingestion.change_detector import ChangeDetector, merge_file_diffs

    head = get_head_commit(repo_path) or "HEAD"

    detector = ChangeDetector(repo_path)
    working_tree_diffs: list = []
    working_tree_paths: list[str] | None = None
    if include_working_tree:
        working_tree_diffs = detector.get_working_tree_changes()
        dirty = {fd.path for fd in working_tree_diffs}
        # Re-diff what the last run indexed from the working tree that no longer
        # diverges from HEAD, so a reverted edit or deleted untracked file drops.
        working_tree_diffs += detector.stale_working_tree_diffs(
            state.get("working_tree_paths") or [], dirty
        )
        working_tree_paths = sorted(dirty)

    file_diffs = merge_file_diffs(
        detector.get_changed_files(base_ref, head),
        working_tree_diffs,
    )
    return file_diffs, working_tree_paths


async def _partial_analysis(
    repo_path: Path,
    base_ref: str,
    file_diffs: list,
    parsed_files: list,
    source_map: dict,
    graph_builder: Any,
    git_meta_map: dict,
) -> tuple[Any, Any, Any]:
    """Partial health, dead-code and doc-drift reports over the changed files.

    Returns ``(partial_health_report, dead_code_report, doc_drift_report)``.
    """
    from ...pipeline.incremental import (
        DocDriftUpdate,
        load_stored_coverage_map,
        load_stored_function_mod_p80,
        load_stored_git_meta,
        load_stored_performance_callers,
        run_doc_drift_partial,
        run_partial_analysis,
    )

    # Stored per-file git fields: ``git_meta_map`` covers only the changed files,
    # and an empty dict reads as "no commits in 90 days" to the dead-code analyzer.
    stored_git_meta = await load_stored_git_meta(repo_path, log=_log.info)
    # Repo-wide p80, so the Function Hotspot gate scores against the full repo.
    stored_function_mod_p80 = await load_stored_function_mod_p80(repo_path, log=_log.info)
    performance_changed_paths = {diff.path for diff in file_diffs}
    performance_changed_paths.update(
        diff.old_path for diff in file_diffs if getattr(diff, "old_path", None)
    )
    stored_performance_callers = await load_stored_performance_callers(
        repo_path, performance_changed_paths, log=_log.info
    )
    # Without a coverage_map the changed files would lose their stored coverage.
    stored_coverage_map = await load_stored_coverage_map(repo_path, log=_log.info)

    partial_health_report, dead_code_report = run_partial_analysis(
        repo_path,
        graph_builder,
        git_meta_map,
        parsed_files,
        file_diffs,
        source_map=source_map,
        stored_git_meta=stored_git_meta,
        stored_performance_callers=stored_performance_callers,
        repo_function_mod_p80=stored_function_mod_p80,
        coverage_map=stored_coverage_map,
        log=_log.info,
    )
    doc_drift_report = run_doc_drift_partial(
        graph_builder,
        source_map,
        repo_path=repo_path,
        log=_log.info,
        update=DocDriftUpdate.from_file_diffs(base_ref, file_diffs),
    )
    return partial_health_report, dead_code_report, doc_drift_report


def _export_kg_state(repo_path: Path, alias: str, kg: Any) -> dict[str, Any] | None:
    """Write ``knowledge-graph.json`` and return its state.json block, or ``None``."""
    if kg is None:
        return None
    from ...analysis.knowledge_graph import build_kg_state, save_knowledge_graph_json

    try:
        save_knowledge_graph_json(repo_path, kg)
        return build_kg_state(kg)
    except Exception:
        _log.warning("knowledge-graph.json export failed for %s", alias, exc_info=True)
    return None


async def _has_persisted_repo_index(repo_path: Path) -> bool:
    """Return whether this repo has persisted index data.

    Local SQLite indexes are identified by their repo-local wiki.db.
    When a shared database is configured, the repository row is the source
    of truth instead; a repo-local wiki.db is not expected to exist.
    """
    from ...persistence.database import get_configured_db_url

    if get_configured_db_url() is None:
        return (repo_path / ".repowise" / "wiki.db").is_file()

    from ...persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from ...persistence.crud import get_repository_by_path

    engine = create_engine(get_configured_db_url())
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            return await get_repository_by_path(session, str(repo_path)) is not None
    finally:
        await engine.dispose()


async def update_single_repo_index(
    repo_path: Path,
    *,
    commit_depth: int = _DEFAULT_COMMIT_LIMIT,
    exclude_patterns: list[str] | None = None,
    include_working_tree: bool = False,
    progress: Any | None = None,
) -> RepoUpdateResult:
    """Refresh the index for a single repo.

    Already-indexed repos (a persisted ``last_sync_commit`` whose commit
    still resolves, plus an existing ``wiki.db``) go through the incremental
    update path — changed-files diff, partial analysis, upsert persistence.
    Never-indexed repos, config changes (``config.yaml`` / health-rules
    fingerprint drift, which invalidates every persisted score), and any
    incremental failure run the full ingestion pipeline instead (index-only —
    no wiki pages).
    """
    from ...repo_config import load_repo_config

    alias = repo_path.name
    state = read_repo_state(repo_path)
    base_ref = state.get("last_sync_commit")
    merged_excludes = await _merged_repo_excludes(repo_path, exclude_patterns)
    repo_config = load_repo_config(repo_path)
    repo_commit_depth = int(repo_config.get("commit_limit", commit_depth))
    repo_follow_renames = bool(repo_config.get("follow_renames", False))

    drift = _drift_requirements(*_config_changes(repo_path, state, repo_config))
    has_persisted_index = await _has_persisted_repo_index(repo_path)
    if drift.requires_full_reindex and has_persisted_index:
        _log.info(
            "workspace_update: %s config fingerprint drifted — full re-index "
            "so health scores reflect the new config",
            alias,
        )

    if (
        not drift.requires_full_reindex
        and base_ref
        and has_persisted_index
        and commit_exists(repo_path, str(base_ref))
    ):
        incremental_result = await _try_incremental_update(
            repo_path, state, str(base_ref), exclude_patterns, include_working_tree
        )
        if incremental_result is not None:
            return incremental_result

    return await _full_index_update(
        repo_path,
        state,
        merged_excludes,
        repo_commit_depth,
        repo_follow_renames,
        drift,
        progress,
    )


class _ConfigDrift(NamedTuple):
    """What a config change demands of this update."""

    requires_full_reindex: bool
    require_git_success: bool
    require_health_success: bool


def _config_changes(
    repo_path: Path, state: dict[str, Any], repo_config: dict[str, Any]
) -> tuple[bool, set[str] | None]:
    """Return ``(config_changed, changed_dependencies)`` against the stored fingerprints.

    A missing stored fingerprint (legacy state) is not drift: the single-repo
    path only stamps it, so legacy repos do not pay a surprise full re-index.
    """
    from ...repo_config import (
        changed_config_dependencies,
        config_dependency_fingerprints,
        config_fingerprint,
    )

    stored_fp = state.get("config_fingerprint")
    config_changed = stored_fp is not None and stored_fp != config_fingerprint(repo_path)
    current_dependency_fps = config_dependency_fingerprints(repo_path, config=repo_config)
    changed_dependencies = (
        changed_config_dependencies(
            state.get("config_dependency_fingerprints"), current_dependency_fps
        )
        if config_changed
        else set()
    )
    return config_changed, changed_dependencies


def _drift_requirements(
    config_changed: bool, changed_dependencies: set[str] | None
) -> _ConfigDrift:
    """Which persisted stores a config change invalidates.

    Workspace indexing has no generation layer, so formatting, distill/MCP and
    generation-only edits are state updates, not a full re-index.
    """
    requires_full_reindex = config_changed and (
        changed_dependencies is None
        or bool((changed_dependencies or set()) - {"state_only", "generation"})
    )
    require_git_success = requires_full_reindex and (
        changed_dependencies is None
        or bool({"traversal", "git_history", "other"} & (changed_dependencies or set()))
    )
    require_health_success = requires_full_reindex and (
        changed_dependencies is None
        or bool(
            {"traversal", "git_history", "health", "other"}
            & (changed_dependencies or set())
        )
    )
    return _ConfigDrift(requires_full_reindex, require_git_success, require_health_success)


async def _try_incremental_update(
    repo_path: Path,
    state: dict[str, Any],
    base_ref: str,
    exclude_patterns: list[str] | None,
    include_working_tree: bool,
) -> RepoUpdateResult | None:
    """The incremental update's result, or ``None`` to fall back to the full pipeline."""
    try:
        return await _incremental_repo_update(
            repo_path,
            state=state,
            base_ref=base_ref,
            exclude_patterns=exclude_patterns,
            include_working_tree=include_working_tree,
            force_full_git=git_history_stale(state),
        )
    except Exception:
        _log.warning(
            "Incremental update failed for %s — falling back to the full pipeline",
            repo_path,
            exc_info=True,
        )
    return None


async def _full_index_update(
    repo_path: Path,
    state: dict[str, Any],
    merged_excludes: list[str],
    commit_depth: int,
    follow_renames: bool,
    drift: _ConfigDrift,
    progress: Any | None,
) -> RepoUpdateResult:
    """Run the full index-only pipeline; a failure becomes an error result."""
    alias = repo_path.name
    try:
        from ...pipeline.full_index import index_repo_full

        result = await index_repo_full(
            repo_path,
            commit_depth=commit_depth,
            exclude_patterns=merged_excludes,
            include_submodules=bool(state.get("include_submodules", False)),
            include_nested_repos=bool(state.get("include_nested_repos", False)),
            follow_renames=follow_renames,
            require_git_success=drift.require_git_success,
            require_health_success=drift.require_health_success,
            progress=progress,
        )

        # index_repo_full already exported knowledge-graph.json; build the
        # state.json summary block so the workspace pass stamps it too.
        kg_state: dict[str, Any] | None = None
        kg = getattr(result, "knowledge_graph_result", None)
        if kg is not None:
            from ...analysis.knowledge_graph import build_kg_state

            kg_state = build_kg_state(kg)

        return RepoUpdateResult(
            alias=alias,
            updated=True,
            file_count=result.file_count,
            symbol_count=result.symbol_count,
            kg_state=kg_state,
            git_history_refreshed=getattr(result, "git_summary", None) is not None,
        )
    except Exception as exc:
        return RepoUpdateResult(
            alias=alias,
            updated=False,
            error=str(exc),
        )
