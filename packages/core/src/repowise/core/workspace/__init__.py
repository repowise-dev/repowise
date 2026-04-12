"""Workspace support — multi-repo detection, configuration, and analysis.

Public API re-exports for the workspace package.
"""

from __future__ import annotations

from .config import (
    WORKSPACE_CONFIG_FILENAME,
    WORKSPACE_DATA_DIR,
    RepoEntry,
    WorkspaceConfig,
    ensure_workspace_data_dir,
    find_workspace_root,
)
from .scanner import (
    DiscoveredRepo,
    ScanResult,
    scan_for_repos,
)
from .registry import (
    RepoContext,
    RepoRegistry,
)
from .update import (
    RepoUpdateResult,
    check_repo_staleness,
    run_cross_repo_hooks,
    update_single_repo_index,
    update_workspace,
)

__all__ = [
    # Scanner
    "DiscoveredRepo",
    "ScanResult",
    "scan_for_repos",
    # Config
    "WORKSPACE_CONFIG_FILENAME",
    "WORKSPACE_DATA_DIR",
    "RepoEntry",
    "WorkspaceConfig",
    "ensure_workspace_data_dir",
    "find_workspace_root",
    # Registry
    "RepoContext",
    "RepoRegistry",
    # Update
    "RepoUpdateResult",
    "check_repo_staleness",
    "run_cross_repo_hooks",
    "update_single_repo_index",
    "update_workspace",
]
