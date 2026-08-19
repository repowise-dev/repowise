"""Data containers for editor-file generators.

These frozen dataclasses decouple DB fetching from template rendering.
All fields use basic Python types so they can be constructed directly in tests
without any DB or filesystem dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _render_tool_table() -> str:
    # Local import: tool_table is a leaf module; importing lazily keeps data.py
    # free of import-order coupling for the many tests that build these
    # dataclasses directly.
    from .tool_table import render_tool_table

    return render_tool_table()


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
    # Bug-fix history for the same file. ``fix_count`` is the windowed
    # production-code fix count (``GitMetadata.prior_defect_count``), and
    # ``last_fix_age`` is pre-rendered ("2 weeks ago") because a count with no
    # recency reads as an accusation about 2019. ``bug_magnet`` is only ever set
    # alongside an age, per the recency contract in ``types/health.ts``.
    fix_count: int = 0
    bug_magnet: bool = False
    last_fix_age: str | None = None


@dataclass(frozen=True)
class DecisionSummary:
    title: str
    status: str  # active | deprecated | superseded | proposed
    rationale: str  # first ~100 chars of decision.rationale
    decision: str = ""  # what was chosen (first ~120 chars)


@dataclass(frozen=True)
class CodeHealthBlock:
    """Compact summary for the generated CLAUDE.md ``## Code health`` section."""

    hotspot_health: float
    average_health: float
    worst_score: float
    worst_path: str
    hotspot_trend: str = "stable"
    # Maintainability pillar headline (NLOC-weighted average over the per-file
    # maintainability scores). ``None`` until the split populates the column, so
    # the section omits it rather than printing a misleading 10.0.
    maintainability_average: float | None = None
    # Performance pillar headline (NLOC-weighted average over the per-file
    # performance scores: static performance RISK). ``None`` when unmeasured, so
    # the section omits the line rather than printing a misleading 10.0.
    performance_average: float | None = None
    # Honest performance headline: the open finding count, its density per 10K
    # covered LOC, and how much of the analyzed code a perf detector actually ran
    # on. An agent reading a bare 9.9/10 should still see "N findings" and "perf
    # ran on X% of the code" so a mostly-unsupported-language repo never reads as
    # verified-fast. ``performance_coverage_pct`` is ``None`` when no code file
    # carries a supported language.
    performance_findings: int = 0
    performance_findings_density: float | None = None
    performance_coverage_pct: float | None = None
    performance_skipped_files: int = 0
    performance_unsupported_languages: list[tuple[str, int]] = field(default_factory=list)
    critical_biomarkers: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class KGLayerSummary:
    name: str
    file_count: int
    description: str


@dataclass(frozen=True)
class KGTourStepSummary:
    order: int
    title: str
    primary_file: str
    reason: str = ""


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
    code_health: CodeHealthBlock | None = None
    kg_layers: list[KGLayerSummary] = field(default_factory=list)
    kg_tour: list[KGTourStepSummary] = field(default_factory=list)
    # Rendered MCP tool table (single source: tool_table.py). A data field
    # rather than a Jinja global so any environment can render the template.
    tool_table_md: str = field(default_factory=lambda: _render_tool_table())


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
    # Rendered MCP tool table (single source: tool_table.py).
    tool_table_md: str = field(default_factory=lambda: _render_tool_table())
