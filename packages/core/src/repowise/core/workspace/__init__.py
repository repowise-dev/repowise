"""Workspace support — multi-repo detection, configuration, and analysis.

Public API re-exports for the workspace package.
"""

from __future__ import annotations

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
from .cross_repo import (
    CROSS_REPO_EDGES_FILENAME,
    CrossRepoCoChange,
    CrossRepoOverlay,
    CrossRepoPackageDep,
    load_overlay,
    run_cross_repo_analysis,
    save_overlay,
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
from .diagnostics import (
    WEAK_LINK_CONFIDENCE_THRESHOLD,
    ExtractionDiagnostics,
    OrphanProvider,
    RepoDiagnostics,
    UnmatchedConsumer,
    UnmatchedReason,
    build_diagnostics,
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
from .architecture_metrics import (
    ArchitectureMetrics,
    NodeArchitectureRole,
    architecture_score,
    compute_architecture_metrics,
)

__all__ = [
    # Scanner
    "DiscoveredRepo",
    "ScanResult",
    "scan_for_repos",
    # Config
    "WORKSPACE_CONFIG_FILENAME",
    "WORKSPACE_DATA_DIR",
    "ContractConfig",
    "ManualContractLink",
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
    # Cross-repo intelligence
    "CROSS_REPO_EDGES_FILENAME",
    "CrossRepoCoChange",
    "CrossRepoOverlay",
    "CrossRepoPackageDep",
    "load_overlay",
    "run_cross_repo_analysis",
    "save_overlay",
    # Contracts (Phase 4)
    "CONTRACTS_FILENAME",
    "Contract",
    "ContractLink",
    "ContractStore",
    "load_contract_store",
    "run_contract_extraction",
    "save_contract_store",
    # Extraction diagnostics
    "WEAK_LINK_CONFIDENCE_THRESHOLD",
    "ExtractionDiagnostics",
    "OrphanProvider",
    "RepoDiagnostics",
    "UnmatchedConsumer",
    "UnmatchedReason",
    "build_diagnostics",
    # System graph
    "EDGE_KINDS",
    "SYSTEM_GRAPH_FILENAME",
    "SystemEdge",
    "SystemGraph",
    "SystemNode",
    "build_system_graph",
    "load_system_graph",
    "run_system_graph_build",
    "save_system_graph",
    # Architecture metrics (Phase 6)
    "ArchitectureMetrics",
    "NodeArchitectureRole",
    "architecture_score",
    "compute_architecture_metrics",
]
