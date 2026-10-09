"""The declaration registry, folded without deduplication or bounds.

The unit here is a declaration, not a package: one row per name and manifest,
so a monorepo's per-package manifests stay distinguishable. That is what
separates this from :mod:`.summary`, which collapses the same rows to one entry
per package and pages them. Neither is a view of the other.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .records import field

#: Sort prominence, not a judgement of importance — a stable order to render in.
_CATEGORY_ORDER = {"framework": 0, "service": 1, "tool": 2, "library": 3}
_UNRANKED = 9


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """One declared third-party dependency, as one manifest declared it."""

    name: str
    display_name: str
    ecosystem: str
    category: str
    io_kind: str | None
    version: str | None
    declared_in: str
    is_dev_dep: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "ecosystem": self.ecosystem,
            "category": self.category,
            "io_kind": self.io_kind,
            "version": self.version,
            "declared_in": self.declared_in,
            "is_dev_dep": self.is_dev_dep,
        }


@dataclass(frozen=True, slots=True)
class PackageRegistry:
    """Every declaration in the repository, ordered and counted."""

    items: list[RegistryEntry]
    total: int
    prod_count: int
    dev_count: int
    ecosystems: list[str]
    manifests: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "total": self.total,
            "prod_count": self.prod_count,
            "dev_count": self.dev_count,
            "ecosystems": list(self.ecosystems),
            "manifests": list(self.manifests),
        }


def build_registry(declarations: Iterable[Any]) -> PackageRegistry:
    """Fold declaration records into the unfiltered, undeduplicated registry."""
    items = sorted(
        (
            RegistryEntry(
                name=field(row, "name", "") or "",
                display_name=field(row, "display_name", "") or field(row, "name", "") or "",
                ecosystem=field(row, "ecosystem", "") or "",
                category=field(row, "category", "") or "",
                io_kind=field(row, "io_kind", None),
                version=field(row, "version", None),
                declared_in=field(row, "declared_in", "") or "",
                is_dev_dep=bool(field(row, "is_dev_dep", False)),
            )
            for row in declarations
        ),
        key=lambda e: (_CATEGORY_ORDER.get(e.category, _UNRANKED), e.name.lower(), e.declared_in),
    )
    return PackageRegistry(
        items=items,
        total=len(items),
        prod_count=sum(1 for e in items if not e.is_dev_dep),
        dev_count=sum(1 for e in items if e.is_dev_dep),
        ecosystems=sorted({e.ecosystem for e in items}),
        manifests=sorted({e.declared_in for e in items}),
    )


__all__ = ["PackageRegistry", "RegistryEntry", "build_registry"]
