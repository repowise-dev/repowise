"""``repowise update`` command package.

Split out of a single 1400-line module into focused submodules (mirroring
``init_cmd``). The public ``update_command`` plus the helpers other modules and
the test-suite import are re-exported here so ``repowise.cli.commands.update_cmd``
remains a stable import surface.
"""

from __future__ import annotations

from .command import update_command
from .incremental import (
    _build_filtered_changed_paths,
    _build_repo_graph,
    _build_update_vector_store,
    _rebuild_graph_and_git,
    _run_partial_analysis,
)
from .mode import _infer_legacy_docs_enabled, _resolve_index_only_mode
from .persistence import (
    _git_metadata_to_dict,
    _persist_incremental_commits,
    _persist_index_only_update,
    _persist_partial_health,
    _run_full_health_rescore,
)
from .reporting import _render_update_report
from .workspace import _refresh_workspace_editor_project_files, _workspace_update

__all__ = [
    "_build_filtered_changed_paths",
    "_build_repo_graph",
    "_build_update_vector_store",
    "_git_metadata_to_dict",
    "_infer_legacy_docs_enabled",
    "_persist_incremental_commits",
    "_persist_index_only_update",
    "_persist_partial_health",
    "_rebuild_graph_and_git",
    "_refresh_workspace_editor_project_files",
    "_render_update_report",
    "_resolve_index_only_mode",
    "_run_full_health_rescore",
    "_run_partial_analysis",
    "_workspace_update",
    "update_command",
]
