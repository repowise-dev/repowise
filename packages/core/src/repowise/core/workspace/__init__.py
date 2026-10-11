"""Workspace support — multi-repo detection, configuration, and analysis.

This package ``__init__`` is a **lazy re-export shim** and imports nothing at
module scope. The public names below resolve on first attribute access via
PEP 562 ``__getattr__``, so ``from repowise.core.workspace import
WorkspaceConfig`` still works, while importing a sibling submodule costs only
that submodule.

Why it matters: a package ``__init__`` runs on *any* submodule import, so
``import repowise.core.workspace.config`` — a leaf whose only dependency is
yaml, and the first thing ``repowise mcp`` touches — used to be charged the
whole analysis subsystem: sqlalchemy via :mod:`.registry`, networkx via
:mod:`.architecture_metrics`, and the ingestion graph via :mod:`.update`.
Roughly a second of import, to resolve two functions.
"""

from __future__ import annotations

from typing import Any

#: Public name -> defining submodule; the source of truth for ``__all__``,
#: ``__getattr__`` and ``__dir__``.
_EXPORTS: dict[str, str] = {
    "ArchitectureMetrics": "architecture_metrics",
    "NodeArchitectureRole": "architecture_metrics",
    "architecture_score": "architecture_metrics",
    "compute_architecture_metrics": "architecture_metrics",
    "WORKSPACE_CONFIG_FILENAME": "config",
    "WORKSPACE_DATA_DIR": "config",
    "ContractConfig": "config",
    "ManualContractLink": "config",
    "RepoEntry": "config",
    "WorkspaceConfig": "config",
    "ensure_workspace_data_dir": "config",
    "find_workspace_root": "config",
    "CONTRACTS_FILENAME": "contracts",
    "Contract": "contracts",
    "ContractLink": "contracts",
    "ContractStore": "contracts",
    "load_contract_store": "contracts",
    "run_contract_extraction": "contracts",
    "save_contract_store": "contracts",
    "CROSS_REPO_EDGES_FILENAME": "cross_repo",
    "CrossRepoCoChange": "cross_repo",
    "CrossRepoOverlay": "cross_repo",
    "CrossRepoPackageDep": "cross_repo",
    "CrossRepoPackageDiagnostic": "cross_repo",
    "load_overlay": "cross_repo",
    "run_cross_repo_analysis": "cross_repo",
    "save_overlay": "cross_repo",
    "WEAK_LINK_CONFIDENCE_THRESHOLD": "diagnostics",
    "ExtractionDiagnostics": "diagnostics",
    "OrphanProvider": "diagnostics",
    "RepoDiagnostics": "diagnostics",
    "UnmatchedConsumer": "diagnostics",
    "UnmatchedReason": "diagnostics",
    "build_diagnostics": "diagnostics",
    "RepoContext": "registry",
    "RepoRegistry": "registry",
    "DiscoveredRepo": "scanner",
    "ScanResult": "scanner",
    "scan_for_repos": "scanner",
    "EDGE_KINDS": "system_graph",
    "SYSTEM_GRAPH_FILENAME": "system_graph",
    "SystemEdge": "system_graph",
    "SystemGraph": "system_graph",
    "SystemNode": "system_graph",
    "build_system_graph": "system_graph",
    "load_system_graph": "system_graph",
    "run_system_graph_build": "system_graph",
    "save_system_graph": "system_graph",
    "RepoUpdateResult": "update",
    "check_repo_staleness": "update",
    "reconcile_repo_head_commit": "update",
    "run_cross_repo_hooks": "update",
    "update_single_repo_index": "update",
    "update_workspace": "update",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve a public export on first access, importing only its module."""
    module = _EXPORTS.get(name)
    if module is None:
        # AttributeError, not KeyError: ``from repowise.core.workspace import
        # contracts`` relies on the import system falling back to a submodule.
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value  # cache: subsequent accesses skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
