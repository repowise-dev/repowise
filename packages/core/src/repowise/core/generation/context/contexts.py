"""Context dataclasses passed to the generation Jinja2 templates.

One dataclass per template; extracted from the former context_assembler.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Context dataclasses — one per template
# ---------------------------------------------------------------------------


@dataclass
class FilePageContext:
    file_path: str
    language: str
    docstring: str | None
    symbols: list[dict[str, Any]]
    imports: list[str]
    exports: list[str]
    file_source_snippet: str
    pagerank_score: float
    betweenness_score: float
    community_id: int
    dependents: list[str]
    dependencies: list[str]
    is_api_contract: bool
    is_entry_point: bool
    is_test: bool
    parse_errors: list[str]
    estimated_tokens: int
    # Documentation category (code/config/doc/data/pipeline) — the file_page
    # template adapts its summary guidance to this.
    file_category: str = "code"
    rag_context: list[str] = field(default_factory=list)
    git_metadata: dict | None = None
    co_change_pages: list[dict] = field(default_factory=list)
    dead_code_findings: list[dict] = field(default_factory=list)
    depth: str = "standard"
    dependency_summaries: dict[str, str] = field(default_factory=dict)
    # Graph intelligence (Phase 5 enrichment)
    call_graph: list[dict] = field(default_factory=list)
    heritage: list[dict] = field(default_factory=list)
    community_label: str = ""
    community_cohesion: float = 0.0
    # Architectural decisions touching this file (extracted by
    # DecisionExtractor — inline WHY/DECISION markers, README mining,
    # git archaeology). Kept short on purpose; the module-page renders
    # the full list.
    decision_records: list[dict] = field(default_factory=list)
    # KG layer context (populated when knowledge graph available)
    kg_layer_name: str = ""
    # Stable slug id of the layer (``layer:<slug>``) — used to join a file
    # page to its layer page; ``kg_layer_name`` is the mutable display label.
    kg_layer_id: str = ""
    kg_layer_description: str = ""
    kg_layer_role: str = ""
    kg_neighbors: list[dict] = field(default_factory=list)
    kg_tour_step: dict | None = None
    kg_tags: list[str] = field(default_factory=list)
    kg_node_summary: str = ""


@dataclass
class SymbolSpotlightContext:
    symbol_name: str
    qualified_name: str
    kind: str
    signature: str
    docstring: str | None
    file_path: str
    decorators: list[str]
    is_async: bool
    complexity_estimate: int
    callers: list[str]
    source_body: str | None = None


@dataclass
class ModulePageContext:
    module_path: str
    language: str
    total_symbols: int
    public_symbols: int
    entry_points: list[str]
    dependencies: list[str]
    dependents: list[str]
    pagerank_mean: float
    files: list[str]
    # Graph intelligence enrichment
    file_summaries: dict[str, str] = field(default_factory=dict)
    community_label: str = ""
    community_cohesion: float = 0.0
    key_classes: list[dict] = field(default_factory=list)
    # Phase 2 enrichment: surfaced when available, gracefully degrades.
    decision_records: list[dict] = field(default_factory=list)
    dead_code_findings: list[dict] = field(default_factory=list)
    external_systems: list[dict] = field(default_factory=list)
    # Top files inside the module by PageRank, for the "key files" section.
    key_files: list[dict] = field(default_factory=list)
    top_owners: list[dict] = field(default_factory=list)


@dataclass
class LayerPageContext:
    layer_name: str
    layer_description: str
    file_count: int
    # Stable slug id (``layer:<slug>``) — the layer page's target_path and
    # page_id derive from this, never from the mutable ``layer_name``.
    layer_id: str = ""
    key_files: list[dict] = field(default_factory=list)
    deps_out: list[dict] = field(default_factory=list)
    deps_in: list[dict] = field(default_factory=list)
    tour_steps: list[dict] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    edge_connectors: list[str] = field(default_factory=list)
    # Deterministic per-layer architecture diagram (mermaid source), embedded
    # into the page after generation. Empty when the KG can't produce one.
    diagram_mermaid: str = ""


@dataclass
class SccPageContext:
    scc_id: str
    files: list[str]
    cycle_description: str
    total_symbols: int
    member_symbols: list[dict] = field(default_factory=list)
    # [{"file_path": str, "symbols": [{"name": str, "signature": str, "docstring": str}]}]
    cross_imports: list[dict] = field(default_factory=list)
    # [{"from": str, "to": str}]


@dataclass
class _TopFile:
    """Helper for repo overview top-files list."""

    path: str
    score: float


@dataclass
class RepoOverviewContext:
    repo_name: str
    is_monorepo: bool
    packages: list[Any]  # PackageInfo objects
    language_distribution: dict[str, float]
    total_files: int
    total_loc: int
    entry_points: list[str]
    top_files_by_pagerank: list[_TopFile]
    circular_dependency_count: int
    # Graph intelligence enrichment
    communities: list[dict] = field(default_factory=list)
    execution_flows: list[dict] = field(default_factory=list)
    # Phase 2: third-party dependencies + headline architectural decisions
    external_systems: list[dict] = field(default_factory=list)
    decision_records: list[dict] = field(default_factory=list)


@dataclass
class ArchitectureDiagramContext:
    repo_name: str
    nodes: list[str]
    edges: list[tuple[str, str]]
    communities: dict[int, list[str]]
    scc_groups: list[list[str]]


@dataclass
class ApiContractContext:
    file_path: str
    language: str
    raw_content: str
    endpoints: list[str]
    schemas: list[str]


@dataclass
class InfraPageContext:
    file_path: str
    language: str
    raw_content: str
    targets: list[str]
