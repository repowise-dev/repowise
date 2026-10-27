"""Dependency-registry response models (``external_systems`` table)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ExternalSystemEntry(BaseModel):
    """One declared third-party dependency."""

    name: str
    display_name: str
    ecosystem: str  # npm | pypi | cargo | gomod | nuget | ...
    category: str  # framework | service | tool | library
    io_kind: str | None = None  # db | network | filesystem | subprocess | lock | null
    version: str | None = None
    declared_in: str  # manifest path, e.g. "packages/web/package.json"
    is_dev_dep: bool = False


class ExternalSystemsResponse(BaseModel):
    """The full dependency registry for a repository."""

    items: list[ExternalSystemEntry]
    total: int
    prod_count: int
    dev_count: int
    ecosystems: list[str]
    manifests: list[str]


ExternalSystemLinkState = Literal["linked", "unlinked"]
ExternalSystemsSummaryScope = Literal["primary", "all"]
ExternalSystemMatchBasis = Literal["exact", "subpath", "mapped", "mixed", "unresolved"]


class ExternalSystemSummaryEntry(BaseModel):
    """One canonical package with declaration and graph-usage aggregates."""

    package_key: str
    name: str
    display_name: str
    ecosystem: str
    category: str
    io_kind: str | None = None
    runtime_declared: bool
    dev_declared: bool
    declaration_count: int
    manifest_count: int
    versions: list[str]
    versions_total: int
    versions_truncated: bool
    multiple_versions: bool
    external_node_count: int
    import_edge_count: int
    importing_file_count: int
    link_state: ExternalSystemLinkState


class ExternalSystemsSummaryResponse(BaseModel):
    """Bounded package summaries for the external-dependency scan surface."""

    items: list[ExternalSystemSummaryEntry]
    returned: int
    total_packages: int
    limit: int
    offset: int
    truncated: bool
    scope: ExternalSystemsSummaryScope
    excluded_declarations: int
    total_declarations: int
    runtime_packages: int
    dev_only_packages: int
    observed_packages: int
    linked_packages: int
    unlinked_packages: int
    linked_without_imports: int
    ecosystems: list[str]
    manifest_count: int


class ExternalSystemGraphTarget(BaseModel):
    """One persisted external graph node linked to the selected package."""

    node_id: str
    match_basis: Literal["exact", "subpath", "mapped"]


class ExternalSystemRelationshipNode(BaseModel):
    """A first-party graph community that imports the selected package."""

    aggregate_key: str
    label: str
    community_id: int
    importing_file_count: int
    import_edge_count: int
    top_file: str | None = None


class ExternalSystemRelationshipEdge(BaseModel):
    source: str
    target: str
    import_edge_count: int


class ExternalSystemRelationshipGraphResponse(BaseModel):
    """Bounded aggregate-first relationship graph for one declared package."""

    package_key: str
    package_name: str
    package_node_id: str
    match_basis: ExternalSystemMatchBasis
    matched_external_nodes: list[ExternalSystemGraphTarget]
    matched_external_nodes_total: int
    matched_external_nodes_truncated: bool
    evidence_target_limit: int
    evidence_truncated: bool
    nodes: list[ExternalSystemRelationshipNode]
    edges: list[ExternalSystemRelationshipEdge]
    aggregate_total: int
    aggregate_returned: int
    edge_total: int
    edge_returned: int
    importing_file_total: int
    import_edge_total: int
    node_limit: int
    edge_limit: int
    truncated: bool
    scope: ExternalSystemsSummaryScope


class ExternalSystemImportingFile(BaseModel):
    path: str
    language: str
    import_edge_count: int
    matched_external_node_count: int


class ExternalSystemImportingFilesResponse(BaseModel):
    """One independently bounded page of files behind an aggregate node."""

    package_key: str
    aggregate_key: str
    items: list[ExternalSystemImportingFile]
    total: int
    returned: int
    limit: int
    offset: int
    truncated: bool
    scope: ExternalSystemsSummaryScope


class PackageUsageDeclaration(BaseModel):
    package_key: str
    version: str | None = None
    declared_in: str
    dev: bool = False


class PackageUsageReachability(BaseModel):
    #: reachable | unreachable | test | unknown
    state: str
    #: import_graph | dead_code | is_test | no_import_path | no_entry_points | not_in_graph
    basis: str


class PackageUsageModule(BaseModel):
    """One module the file imports and how it was matched to the package."""

    name: str
    mapping: Literal["declared", "inferred"]


class PackageUsageSite(BaseModel):
    """One file importing the package."""

    path: str
    modules: list[PackageUsageModule]
    symbols: list[str]
    mapping: Literal["declared", "inferred"]
    line: int | None = None
    statement: str | None = None
    component: str | None = None
    owners: list[str]
    #: Declared owners that are not teams, by kind (individual, unresolved): counted, never named.
    owner_counts: dict[str, int] | None = None
    reachability: PackageUsageReachability


class PackageUsageComponent(BaseModel):
    name: str
    files: int
    owners: list[str]


class PackageUsageSummary(BaseModel):
    files: int
    components: int
    reachability: dict[str, int]
    inferred_files: int


class PackageUsageResponse(BaseModel):
    """Where one package is imported: sites, components, owner teams, reachability."""

    package: str
    ecosystem: str | None = None
    declared: list[PackageUsageDeclaration]
    summary: PackageUsageSummary
    sites: list[PackageUsageSite]
    components: list[PackageUsageComponent]
    method: dict[str, str]
    note: str | None = None
