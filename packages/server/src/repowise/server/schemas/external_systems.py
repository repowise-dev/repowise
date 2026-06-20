"""Dependency-registry response models (``external_systems`` table)."""

from __future__ import annotations

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
