"""Data containers for editor-file generators.

These frozen dataclasses decouple DB fetching from template rendering.
All fields use basic Python types so they can be constructed directly in tests
without any DB or filesystem dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TechStackItem:
    name: str
    version: str | None
    category: str  # "language" | "framework" | "database" | "infra"


@dataclass(frozen=True)
class KeyModule:
    name: str  # display name, e.g. "src/api"
    purpose: str  # short description (~80 chars)
    file_count: int
    owner: str | None


@dataclass(frozen=True)
class HotspotFile:
    path: str
    churn_percentile: float
    commit_count_90d: int
    owner: str | None


@dataclass(frozen=True)
class DecisionSummary:
    title: str
    status: str  # active | deprecated | superseded | proposed
    rationale: str  # first ~100 chars of decision.rationale


@dataclass(frozen=True)
class EditorFileData:
    repo_name: str
    indexed_at: str  # date only: "2026-03-28"
    indexed_commit: str  # short SHA of HEAD at index time, e.g. "a1b2c3d"
    architecture_summary: str  # 2-4 sentences from repo_overview page
    key_modules: list[KeyModule] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    tech_stack: list[TechStackItem] = field(default_factory=list)
    hotspots: list[HotspotFile] = field(default_factory=list)
    decisions: list[DecisionSummary] = field(default_factory=list)
    build_commands: dict[str, str] = field(default_factory=dict)
    avg_confidence: float = 0.0


# ---------------------------------------------------------------------------
# Workspace-level data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceRepoSummary:
    """Per-repo summary row within a workspace CLAUDE.md."""

    alias: str
    is_primary: bool
    file_count: int
    symbol_count: int
    hotspot_count: int
    entry_points: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkspaceEditorFileData:
    """All data needed to render the workspace-level CLAUDE.md template."""

    workspace_name: str
    workspace_root: str  # absolute path string (for display only)
    repos: list[WorkspaceRepoSummary] = field(default_factory=list)
    default_repo: str = ""
    co_changes: list[dict] = field(default_factory=list)  # from cross_repo_edges.json
    package_deps: list[dict] = field(default_factory=list)  # package dep entries
    contract_links: list[dict] = field(default_factory=list)  # matched contract links
    contracts_by_type: dict[str, int] = field(default_factory=dict)  # {"http": 5, …}
