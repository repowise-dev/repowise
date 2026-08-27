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
