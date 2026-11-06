"""Data containers for editor-file generators.

These frozen dataclasses decouple DB fetching from template rendering.
All fields use basic Python types so they can be constructed directly in tests
without any DB or filesystem dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repowise.core.persistence.crud.analysis.performance import PerformanceHeadline

from .sections import DEFAULT_SECTIONS


def _render_tool_table(*, keyless: bool = False) -> str:
    # Local import: tool_table is a leaf module; importing lazily keeps data.py
    # free of import-order coupling for the many tests that build these
    # dataclasses directly.
    from .tool_table import render_tool_table

    return render_tool_table(keyless=keyless)


@dataclass(frozen=True)
class TechStackItem:
    name: str
    version: str | None
    category: str  # "language" | "framework" | "database" | "infra"


@dataclass(frozen=True)
class KeyModule:
    name: str  # display name, e.g. "src/api"
    purpose: str  # short description (~80 chars)
    owner: str | None


@dataclass(frozen=True)
class HotspotFile:
    path: str
    churn_percentile: float
    commit_count_90d: int
    owner: str | None
    # Bug-fix history for the same file. ``fix_count`` is the production-code
    # fix count over the prior-defect window (``GitMetadata.prior_defect_count``,
    # see ``fix_window``), and ``last_fix_date`` (``YYYY-MM-DD``) anchors it,
    # because a count with no recency reads as an accusation about 2019; a date
    # stays true between regenerations. ``bug_magnet`` is only ever set
    # alongside a date, per the recency contract in ``types/health.ts``.
    fix_count: int = 0
    bug_magnet: bool = False
    last_fix_date: str | None = None

    @property
    def fix_window(self) -> str:
        """The window ``fix_count`` covers, as the line prints it ("6 months")."""
        # Local import: the constants module loads the language registry, which
        # the many tests that build these dataclasses directly do not need.
        from repowise.core.ingestion.git_indexer._constants import PRIOR_DEFECT_WINDOW_LABEL

        return PRIOR_DEFECT_WINDOW_LABEL


@dataclass(frozen=True)
class DecisionSummary:
    title: str
    status: str  # active | deprecated | superseded | proposed
    rationale: str  # first ~100 chars of decision.rationale
    decision: str = ""  # what was chosen (first ~120 chars)
    #: Pre-rendered mark for a line a person did not sign, else "". Empty in
    #: the ordinary case, so the common line costs no extra tokens.
    signed_by: str = ""


@dataclass(frozen=True)
class CodeHealthBlock:
    """Compact summary for the generated CLAUDE.md ``## Code health`` section."""

    hotspot_health: float
    average_health: float
    worst_score: float | None
    worst_path: str | None
    # The band word for ``average_health``, so the line reads the same
    # direction as every other surface: a bare "6.9/10" beside a risk-shaped
    # label is the one reading that inverts.
    band: str = ""
    # "analysed N of M code files" when health left some out; ``None`` otherwise.
    analysed_note: str | None = None
    # ``None`` until two snapshots exist; the section then omits the label.
    hotspot_trend: str | None = None
    # Maintainability pillar headline (NLOC-weighted average over the per-file
    # maintainability scores). ``None`` until the split populates the column, so
    # the section omits it rather than printing a misleading 10.0.
    maintainability_average: float | None = None
    # Performance pillar headline (NLOC-weighted average over the per-file
    # performance scores: static performance RISK). ``None`` when unmeasured, so
    # the section omits the line rather than printing a misleading 10.0.
    performance_average: float | None = None
    # Honest performance headline: the open causes (worth fixing now, in
    # production code, overall) and how much of the analyzed code a perf
    # detector actually ran on, so a bare 9.9/10 on a mostly-unsupported-language
    # repo never reads as verified-fast. ``performance_coverage_pct`` is ``None``
    # when no code file carries a supported language.
    performance_causes: PerformanceHeadline | None = None
    performance_coverage_pct: float | None = None
    performance_skipped_files: int = 0
    performance_unsupported_languages: list[tuple[str, int]] = field(default_factory=list)
    # The lead of the shared Fix-first queue: ``title``, ``where``
    # (``path:line``) and ``why`` per item, at most three.
    fix_first: list[dict] = field(default_factory=list)


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
    architecture_summary: str  # lead sentence of the repo_overview page
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
    index_scope: dict = field(default_factory=dict)
    sections: frozenset[str] = DEFAULT_SECTIONS

    @property
    def keyless(self) -> bool:
        """Whether the index was built with no model: template prose, no semantic search."""
        scope = self.index_scope
        search = scope.get("search") if isinstance(scope.get("search"), dict) else {}
        return (
            scope.get("content_provenance") in ("template", "none")
            and search.get("semantic") == "unavailable"
        )

    @property
    def model_command(self) -> str:
        """The command that has a model write this index's documentation."""
        from repowise.core.index_scope import model_command

        return model_command(self.index_scope)

    @property
    def scope_gaps(self) -> list[str]:
        """What this index lacks, for the header; empty when nothing does."""
        scope = self.index_scope
        upgrade = scope.get("upgrade") if isinstance(scope.get("upgrade"), dict) else {}
        gaps = []
        if self.keyless:
            # The CLI's mode names (cli/ui/mode_names.py), which core cannot import.
            if scope.get("content_provenance") == "none":
                gaps.append("Index only (no documentation, full-text search)")
            else:
                gaps.append("Built from code (no model, full-text search)")
        if scope.get("git_tier") == "essential":
            gaps.append("essential Git history only")
        if self.upgrade_pending:
            gaps.append(f"model upgrade {upgrade.get('status')}")
        return gaps

    @property
    def upgrade_pending(self) -> bool:
        upgrade = self.index_scope.get("upgrade")
        status = upgrade.get("status") if isinstance(upgrade, dict) else None
        # A keyless index gets its next command from the keyless bullet instead.
        return status in ("pending", "resumable", "failed") and not self.keyless

    @property
    def tool_table_md(self) -> str:
        # Rendered MCP tool table (single source: tool_table.py). Exposed on the
        # data rather than as a Jinja global so any environment can render it.
        return _render_tool_table(keyless=self.keyless)


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
