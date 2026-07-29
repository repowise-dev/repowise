"""Workspace support — multi-repo detection, configuration, and analysis.

Public API re-exports for the workspace package.
"""

from __future__ import annotations

from .architecture_metrics import (
    ArchitectureMetrics,
    NodeArchitectureRole,
    architecture_score,
    compute_architecture_metrics,
)
from .config import (
    WORKSPACE_CONFIG_FILENAME,
    WORKSPACE_DATA_DIR,
    ContractConfig,
    ManualContractLink,
    RepoEntry,
    WorkspaceConfig,
    ensure_workspace_data_dir,
    find_workspace_root,
)
from .contracts import (
    CONTRACTS_FILENAME,
    Contract,
    ContractLink,
    ContractStore,
    load_contract_store,
    run_contract_extraction,
    save_contract_store,
)
from .cross_repo import (
    CROSS_REPO_EDGES_FILENAME,
    CrossRepoCoChange,
    CrossRepoOverlay,
    CrossRepoPackageDep,
    load_overlay,
    run_cross_repo_analysis,
    save_overlay,
)
from .diagnostics import (
    WEAK_LINK_CONFIDENCE_THRESHOLD,
    ExtractionDiagnostics,
    OrphanProvider,
    RepoDiagnostics,
    UnmatchedConsumer,
    UnmatchedReason,
    build_diagnostics,
)
from .registry import (
    RepoContext,
    RepoRegistry,
)
from .scanner import (
    DiscoveredRepo,
    ScanResult,
    scan_for_repos,
)
from .system_graph import (
    EDGE_KINDS,
    SYSTEM_GRAPH_FILENAME,
    SystemEdge,
    SystemGraph,
    SystemNode,
    build_system_graph,
    load_system_graph,
    run_system_graph_build,
    save_system_graph,
)
from .update import (
    RepoUpdateResult,
    check_repo_staleness,
    reconcile_repo_head_commit,
    run_cross_repo_hooks,
    update_single_repo_index,
    update_workspace,
)

__all__ = [
    # Contracts (Phase 4)
    "CONTRACTS_FILENAME",
    # Cross-repo intelligence
    "CROSS_REPO_EDGES_FILENAME",
    # System graph
    "EDGE_KINDS",
    "SYSTEM_GRAPH_FILENAME",
    # Extraction diagnostics
    "WEAK_LINK_CONFIDENCE_THRESHOLD",
    # Config
    "WORKSPACE_CONFIG_FILENAME",
    "WORKSPACE_DATA_DIR",
    # Architecture metrics (Phase 6)
    "ArchitectureMetrics",
    "Contract",
    "ContractConfig",
    "ContractLink",
    "ContractStore",
    "CrossRepoCoChange",
    "CrossRepoOverlay",
    "CrossRepoPackageDep",
    # Scanner
    "DiscoveredRepo",
    "ExtractionDiagnostics",
    "ManualContractLink",
    "NodeArchitectureRole",
    "OrphanProvider",
    # Registry
    "RepoContext",
    "RepoDiagnostics",
    "RepoEntry",
    "RepoRegistry",
    # Update
    "RepoUpdateResult",
    "ScanResult",
    "SystemEdge",
    "SystemGraph",
    "SystemNode",
    "UnmatchedConsumer",
    "UnmatchedReason",
    "WorkspaceConfig",
    "architecture_score",
    "build_diagnostics",
    "build_system_graph",
    "check_repo_staleness",
    "compute_architecture_metrics",
    "ensure_workspace_data_dir",
    "find_workspace_root",
    "load_contract_store",
    "load_overlay",
    "load_system_graph",
    "reconcile_repo_head_commit",
    "run_contract_extraction",
    "run_cross_repo_analysis",
    "run_cross_repo_hooks",
    "run_system_graph_build",
    "save_contract_store",
    "save_overlay",
    "save_system_graph",
    "scan_for_repos",
    "update_single_repo_index",
    "update_workspace",
]
