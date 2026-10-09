"""Composing the bounded package page the external-dependency surface serves.

Session-free by design. The fold takes declaration, link and import-edge
records — dataclasses, dicts or ORM rows — so one implementation answers for a
live database and for a precomputed artifact. Every bound and every honesty
flag it reports (``limit``, ``offset``, ``truncated``, ``returned``,
``total_packages``, ``excluded_declarations``, ``versions_truncated``) is
decided here, because a consumer that re-derives one is a consumer that can
disagree with the UI about what it is not showing.

Composition runs at read time rather than being materialized at index time the
way ``analysis/health/refactoring/opportunity.py`` is. That fold was measured
at 91 ms over 2,283 plans; this one folds a few hundred packages against a few
thousand edges, so a table and a finalizer would buy nothing and cost a
migration. Paging is therefore done in Python, not pushed into SQL. If a
repository ever declares packages in the tens of thousands, materialize this
the same way refactoring did.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .records import Scope, field, in_scope, is_primary_path, package_key

#: The page size the surface asks for when it does not say.
DEFAULT_SUMMARY_LIMIT = 200
#: Versions shown per package before ``versions_truncated`` starts claiming.
SUMMARY_VERSION_LIMIT = 5


@dataclass(frozen=True, slots=True)
class PackageEntry:
    """One canonical package with its declaration and graph-usage aggregates."""

    package_key: str
    name: str
    display_name: str
    ecosystem: str
    category: str
    io_kind: str | None
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
    link_state: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_key": self.package_key,
            "name": self.name,
            "display_name": self.display_name,
            "ecosystem": self.ecosystem,
            "category": self.category,
            "io_kind": self.io_kind,
            "runtime_declared": self.runtime_declared,
            "dev_declared": self.dev_declared,
            "declaration_count": self.declaration_count,
            "manifest_count": self.manifest_count,
            "versions": list(self.versions),
            "versions_total": self.versions_total,
            "versions_truncated": self.versions_truncated,
            "multiple_versions": self.multiple_versions,
            "external_node_count": self.external_node_count,
            "import_edge_count": self.import_edge_count,
            "importing_file_count": self.importing_file_count,
            "link_state": self.link_state,
        }


@dataclass(frozen=True, slots=True)
class PackageSummary:
    """One page of packages plus the totals that say what the page omits."""

    items: list[PackageEntry]
    returned: int
    total_packages: int
    limit: int
    offset: int
    truncated: bool
    scope: str
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "returned": self.returned,
            "total_packages": self.total_packages,
            "limit": self.limit,
            "offset": self.offset,
            "truncated": self.truncated,
            "scope": self.scope,
            "excluded_declarations": self.excluded_declarations,
            "total_declarations": self.total_declarations,
            "runtime_packages": self.runtime_packages,
            "dev_only_packages": self.dev_only_packages,
            "observed_packages": self.observed_packages,
            "linked_packages": self.linked_packages,
            "unlinked_packages": self.unlinked_packages,
            "linked_without_imports": self.linked_without_imports,
            "ecosystems": list(self.ecosystems),
            "manifest_count": self.manifest_count,
        }


@dataclass(slots=True)
class _Declared:
    """One package's declarations, reduced as they arrive."""

    ecosystem: str
    name: str
    display_name: str = ""
    category: str | None = None
    io_kind: str | None = None
    runtime_declared: bool = False
    dev_declared: bool = False
    declaration_count: int = 0
    manifests: set[str] = dataclasses.field(default_factory=set)
    versions: set[str] = dataclasses.field(default_factory=set)


def _least(current: str | None, candidate: str | None) -> str | None:
    """SQL ``MIN`` over text: ignore nulls rather than let one win."""
    if candidate is None:
        return current
    return candidate if current is None or candidate < current else current


def _greatest(current: str, candidate: str | None) -> str:
    """SQL ``MAX`` over text, where the column cannot be null."""
    return candidate if candidate is not None and candidate > current else current


def _declared_packages(
    declarations: Iterable[Any], scope: Scope
) -> tuple[dict[tuple[str, str], _Declared], int]:
    """Group in-scope declarations by identity, counting the ones left out."""
    groups: dict[tuple[str, str], _Declared] = {}
    excluded = 0
    for row in declarations:
        declared_in = field(row, "declared_in", "") or ""
        if not is_primary_path(declared_in):
            excluded += 1
        if not in_scope(declared_in, scope):
            continue
        ecosystem = field(row, "ecosystem", "") or ""
        name = field(row, "name", "") or ""
        group = groups.get((ecosystem, name))
        if group is None:
            group = _Declared(ecosystem=ecosystem, name=name)
            groups[(ecosystem, name)] = group
        group.display_name = _greatest(group.display_name, field(row, "display_name", "") or "")
        group.category = _least(group.category, field(row, "category", None))
        group.io_kind = _least(group.io_kind, field(row, "io_kind", None))
        if field(row, "is_dev_dep", False):
            group.dev_declared = True
        else:
            group.runtime_declared = True
        group.declaration_count += 1
        group.manifests.add(declared_in)
        version = field(row, "version", None)
        if version is not None:
            group.versions.add(version)
    return groups, excluded


_Identity = tuple[str, str]


def _usage(
    links: Iterable[Any], import_edges: Iterable[Any], scope: Scope
) -> tuple[dict[_Identity, set[str]], dict[_Identity, int], dict[_Identity, set[str]]]:
    """Resolve import evidence onto package identities.

    Linked nodes are counted whatever scope is asked for — a node exists or it
    does not — while the edges that reach them are scoped by importing file,
    which is what makes ``observed`` a claim about the repository proper.
    """
    nodes_by_identity: dict[tuple[str, str], set[str]] = {}
    identity_by_node: dict[str, tuple[str, str]] = {}
    for row in links:
        node_id = field(row, "node_id", None)
        if node_id is None:
            continue
        identity = (field(row, "ecosystem", "") or "", field(row, "name", "") or "")
        nodes_by_identity.setdefault(identity, set()).add(node_id)
        identity_by_node[node_id] = identity

    edge_counts: dict[tuple[str, str], int] = {}
    files_by_identity: dict[tuple[str, str], set[str]] = {}
    for row in import_edges:
        source = field(row, "source_path", None)
        if not in_scope(source, scope):
            continue
        identity = identity_by_node.get(field(row, "target_node_id", None))
        if identity is None:
            continue
        edge_counts[identity] = edge_counts.get(identity, 0) + 1
        files_by_identity.setdefault(identity, set()).add(source)
    return nodes_by_identity, edge_counts, files_by_identity


def build_package_summary(
    declarations: Iterable[Any],
    links: Iterable[Any],
    import_edges: Iterable[Any],
    *,
    scope: Scope = "primary",
    limit: int = DEFAULT_SUMMARY_LIMIT,
    offset: int = 0,
) -> PackageSummary:
    """Fold declarations and import evidence into one bounded package page.

    ``declarations`` carry ``ecosystem``, ``name``, ``display_name``,
    ``category``, ``io_kind``, ``version``, ``declared_in`` and ``is_dev_dep``.
    ``links`` name one resolved external graph node each, as ``node_id`` plus
    the ``ecosystem`` and ``name`` it was resolved to. ``import_edges`` carry
    ``source_path`` and ``target_node_id``.
    """
    groups, excluded = _declared_packages(declarations, scope)
    nodes_by_identity, edge_counts, files_by_identity = _usage(links, import_edges, scope)

    entries: list[PackageEntry] = []
    for identity, group in groups.items():
        versions = sorted(group.versions)
        shown = versions[:SUMMARY_VERSION_LIMIT]
        node_count = len(nodes_by_identity.get(identity, ()))
        entries.append(
            PackageEntry(
                package_key=package_key(group.ecosystem, group.name),
                name=group.name,
                display_name=group.display_name or group.name,
                ecosystem=group.ecosystem,
                category=group.category or "",
                io_kind=group.io_kind,
                runtime_declared=group.runtime_declared,
                dev_declared=group.dev_declared,
                declaration_count=group.declaration_count,
                manifest_count=len(group.manifests),
                versions=shown,
                versions_total=len(versions),
                versions_truncated=len(versions) > len(shown),
                multiple_versions=len(versions) > 1,
                external_node_count=node_count,
                import_edge_count=edge_counts.get(identity, 0),
                importing_file_count=len(files_by_identity.get(identity, ())),
                link_state="linked" if node_count else "unlinked",
            )
        )

    entries.sort(
        key=lambda e: (
            not e.runtime_declared,
            -e.importing_file_count,
            e.name.lower(),
            e.ecosystem,
        )
    )
    page = entries[offset : offset + limit]
    linked_packages = sum(1 for e in entries if e.external_node_count)
    return PackageSummary(
        items=page,
        returned=len(page),
        total_packages=len(entries),
        limit=limit,
        offset=offset,
        truncated=offset + len(page) < len(entries),
        scope=scope,
        # Only the primary scope is hiding anything, so only it makes the claim.
        excluded_declarations=excluded if scope == "primary" else 0,
        total_declarations=sum(e.declaration_count for e in entries),
        runtime_packages=sum(1 for e in entries if e.runtime_declared),
        dev_only_packages=sum(1 for e in entries if e.dev_declared and not e.runtime_declared),
        observed_packages=sum(1 for e in entries if e.import_edge_count),
        linked_packages=linked_packages,
        unlinked_packages=len(entries) - linked_packages,
        linked_without_imports=sum(
            1 for e in entries if e.external_node_count and not e.import_edge_count
        ),
        ecosystems=sorted({e.ecosystem for e in entries}),
        manifest_count=len({m for group in groups.values() for m in group.manifests}),
    )


__all__ = [
    "DEFAULT_SUMMARY_LIMIT",
    "SUMMARY_VERSION_LIMIT",
    "PackageEntry",
    "PackageSummary",
    "build_package_summary",
]
