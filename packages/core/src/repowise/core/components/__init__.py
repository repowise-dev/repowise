"""Components: named parts of a repository that code facts roll up to.

Declared in Backstage-shaped catalog files (:mod:`.catalog`), derived from the
index when none are declared (:mod:`.resolve`), validated by
``repowise components check`` (:mod:`.check`), and summarised per component
from existing per-file facts (:mod:`.rollup`).
"""

from __future__ import annotations

from repowise.core.components.catalog import (
    CATALOG_FILES,
    Catalog,
    CatalogIssue,
    Component,
    Group,
    System,
    read_catalog,
)
from repowise.core.components.check import check_catalog
from repowise.core.components.resolve import ResolvedComponents, resolve_components

__all__ = [
    "CATALOG_FILES",
    "Catalog",
    "CatalogIssue",
    "Component",
    "Group",
    "ResolvedComponents",
    "System",
    "check_catalog",
    "read_catalog",
    "resolve_components",
]
