"""Incremental (changed-files) index refresh.

The orchestration that `repowise update` runs for an already-indexed repo:
re-ingest the graph (parse-cache backed), re-index git metadata for the
changed files only, run partial health/dead-code analysis, and upsert the
results — without the full pipeline's delete-then-insert persistence or LLM
generation.

Extracted from the CLI update command so workspace updates can route
already-indexed member repos through the same incremental path instead of
re-running the full init pipeline per repo. The CLI keeps thin wrappers
that delegate here.

Progress/diagnostic messages go through an optional ``log`` callback (the
CLI passes ``console.print``; messages use rich markup). When ``log`` is
omitted the messages are dropped — every one of them annotates a
best-effort step that already degrades gracefully.

Submodules: :mod:`.graph`, :mod:`.loaders`, :mod:`.analysis`,
:mod:`.knowledge_graph`, :mod:`.health`, :mod:`.commits`,
:mod:`.external_systems`, :mod:`.prune` and :mod:`.persist_index`. Every name
is re-exported here. Patch a name where it is looked up: a patch on this
package does not reach lookups inside the submodules.
"""

from __future__ import annotations

from ._common import (
    LogFn,
    _noop_log,
    logger,
)
from .analysis import (
    DocDriftUpdate,
    _drift_symbol_options,
    _performance_only_config,
    run_doc_drift_partial,
    run_partial_analysis,
)
from .commits import (
    _churn_prior,
    _committed_ts,
    _recompute_commit_experience,
    persist_incremental_commits,
    persist_incremental_fix_events,
    reconcile_commit_experience,
    reconcile_commit_offsets,
)
from .external_systems import (
    refresh_external_systems,
)
from .graph import (
    _add_parsed_files,
    _add_synthetic_edges,
    _parse_misses,
    _record_structural_episodes,
    build_filtered_changed_paths,
    build_repo_graph,
    rebuild_graph_and_git,
)
from .health import (
    _differs,
    _history_findings_moved,
    _numbers_moved,
    _refreshed_metric,
    persist_partial_health,
    refresh_unchanged_history,
)
from .knowledge_graph import (
    _carry_forward_kg_enrichment,
    refresh_knowledge_graph,
)
from .loaders import (
    load_stored_coverage_map,
    load_stored_function_mod_p80,
    load_stored_git_meta,
    load_stored_performance_callers,
)
from .persist_index import (
    persist_incremental_index,
)
from .prune import (
    prune_deleted_files,
)

__all__ = [
    "DocDriftUpdate",
    "LogFn",
    "_add_parsed_files",
    "_add_synthetic_edges",
    "_carry_forward_kg_enrichment",
    "_churn_prior",
    "_committed_ts",
    "_differs",
    "_drift_symbol_options",
    "_history_findings_moved",
    "_noop_log",
    "_numbers_moved",
    "_parse_misses",
    "_performance_only_config",
    "_recompute_commit_experience",
    "_record_structural_episodes",
    "_refreshed_metric",
    "build_filtered_changed_paths",
    "build_repo_graph",
    "load_stored_coverage_map",
    "load_stored_function_mod_p80",
    "load_stored_git_meta",
    "load_stored_performance_callers",
    "logger",
    "persist_incremental_commits",
    "persist_incremental_fix_events",
    "persist_incremental_index",
    "persist_partial_health",
    "prune_deleted_files",
    "rebuild_graph_and_git",
    "reconcile_commit_experience",
    "reconcile_commit_offsets",
    "refresh_external_systems",
    "refresh_knowledge_graph",
    "refresh_unchanged_history",
    "run_doc_drift_partial",
    "run_partial_analysis",
]
