"""Workspace-level update orchestration.

Handles staleness detection, parallel multi-repo updates, and cross-repo
analysis hooks. The package re-exports every name the old single module held.
"""

# ruff: noqa: F401
from __future__ import annotations

from .phases import (
    _analysis_and_extraction_phases,
    _boundaries_phase,
    _breaking_change_phase,
    _conformance_phase,
    _log_phase,
    _refresh_contract_facts,
    _run_phases,
    _system_graph_phase,
    analysis_config_changed,
    run_cross_repo_hooks,
)
from .repo import (
    RepoUpdateResult,
    _has_persisted_repo_index,
    _incremental_repo_update,
    _merged_repo_excludes,
    update_single_repo_index,
)
from .state import (
    _ANCESTRY_CACHE_MAX,
    IndexedCommit,
    _ancestry_cache,
    _older_commit,
    check_repo_staleness,
    clear_stale_update_pending,
    commit_exists,
    count_commits_between,
    get_head_commit,
    read_index_synced_at,
    read_repo_state,
    read_state_commit,
    reconcile_repo_head_commit,
    resolve_indexed_commit,
    restamp_changed_sync_commit,
    stamp_workspace_entry,
    sync_workspace_state_from_disk,
    synced_at_iso,
)
from .workspace_pass import (
    _MAX_CONCURRENT_UPDATES,
    _collect_update_results,
    _defer_in_flight_repo,
    _defer_to_running_workspace_update,
    _entries_for_run,
    _finish_workspace_pass,
    _lock_age_seconds,
    _release_lock,
    _release_workspace_lock,
    _repo_is_stale,
    _RepoUpdatePass,
    _select_stale_repos,
    _StaleRepo,
    _try_acquire_lock,
    _try_acquire_workspace_lock,
    _update_stale_repo,
    _write_repo_state,
    update_workspace,
)

__all__ = [
    "IndexedCommit",
    "RepoUpdateResult",
    "analysis_config_changed",
    "check_repo_staleness",
    "clear_stale_update_pending",
    "commit_exists",
    "count_commits_between",
    "get_head_commit",
    "read_index_synced_at",
    "read_repo_state",
    "read_state_commit",
    "reconcile_repo_head_commit",
    "resolve_indexed_commit",
    "restamp_changed_sync_commit",
    "run_cross_repo_hooks",
    "stamp_workspace_entry",
    "sync_workspace_state_from_disk",
    "synced_at_iso",
    "update_single_repo_index",
    "update_workspace",
]
