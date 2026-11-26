"""The declaration-to-external-node match, as a fold over plain records.

The folds in :mod:`.summary` and :mod:`.relationships` take links as given, but
only the persistence layer knew how to build one, so a consumer holding
declarations and graph nodes could not reach them without a database. The
matcher lives here, framework-free, and the CRUD writer calls it too, so the
stored ``external_system_id`` and a link built from plain records cannot
disagree about what resolves.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .jvm_packages import jvm_coordinate_candidates
from .pypi_imports import pypi_import_names
from .records import field, package_key

#: Graph nodes standing for a third-party package carry this ``node_id`` prefix.
EXTERNAL_NODE_PREFIX = "external:"

#: ``language`` of a graph "file" row that is an import target, not a file the
#: index read: a third-party package, or a path an edge names that traversal
#: never parsed (a directory, an excluded or generated file). Traversal drops
#: unknown-language files, so no parsed file carries either tag.
IMPORT_TARGET_LANGUAGES = ("external", "unknown")


@dataclass(frozen=True, slots=True)
class ExternalSystemLink:
    """One external graph node resolved to the package a manifest declared."""

    node_id: str
    ecosystem: str
    name: str

    def as_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "ecosystem": self.ecosystem, "name": self.name}


#: A Go major-version suffix (``/v2``, ``/v10``): the path under it is a
#: different module from the path without it. ``v1`` and ``v0`` are ordinary
#: package names (``k8s.io/api/core/v1``).
_GO_MAJOR_SUFFIX = re.compile(r"v(?:[2-9]|[1-9]\d+)")


def _path_prefixes(external_name: str) -> list[str]:
    """Every proper ``/`` prefix, longest first, so the longest declared module wins.

    A Go import names a package inside a module (``google.golang.org/protobuf/
    encoding/protojson`` under ``google.golang.org/protobuf``), and the module
    can sit any number of segments up. A prefix followed by a major-version
    segment is skipped: ``github.com/x/y/v2/z`` is never module ``github.com/x/y``.
    """
    segments = external_name.split("/")
    return [
        "/".join(segments[:end])
        for end in range(len(segments) - 1, 0, -1)
        if not _GO_MAJOR_SUFFIX.fullmatch(segments[end])
    ]


def _dotted_prefixes(external_name: str) -> list[str]:
    """Every proper ``.`` prefix, longest first (``google.protobuf`` before ``google``)."""
    segments = external_name.split(".")
    return [".".join(segments[:end]) for end in range(len(segments) - 1, 0, -1)]


def declaration_name_candidates(external_name: str) -> tuple[str, ...]:
    """Declaration names to try for one node suffix, most precise first.

    Resolver output is ecosystem-shaped (npm subpaths, Go package paths, Rust
    ``::`` paths, Python dotted modules), so the declared package is a prefix
    of the node rather than the whole of it. Longer prefixes come first, so
    the most specific declared name wins.

    JVM imports are the exception: a Java or Kotlin package never equals its
    Maven ``group:artifact``, so those come from a curated table (see
    :mod:`.jvm_packages`) and sit ahead of the generic prefixes. A name the
    table does not know adds nothing, and a candidate only links when a
    manifest declared it, so non-JVM names are unaffected.
    """
    candidates = [external_name]
    if external_name.startswith("@"):
        parts = external_name.split("/")
        if len(parts) >= 2:
            candidates.append("/".join(parts[:2]))
    candidates.extend(jvm_coordinate_candidates(external_name))
    if "/" in external_name:
        candidates.extend(_path_prefixes(external_name))
    elif "." in external_name and "::" not in external_name:
        candidates.extend(_dotted_prefixes(external_name))
    for separator in ("::", ".", "/"):
        if separator in external_name:
            candidates.append(external_name.split(separator, 1)[0])
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def _index_names(name: str, ecosystem: str) -> tuple[str, ...]:
    """The names one declaration answers to as an import target.

    A PyPI distribution is imported under a module name (``Pillow`` as
    ``PIL``, ``Django`` as ``django``), so it answers to those as well as its
    own; see :mod:`.pypi_imports`.
    """
    if ecosystem == "pypi":
        return tuple(dict.fromkeys((name, *pypi_import_names(name))))
    return (name,)


def build_declaration_index(
    declarations: Iterable[Any],
) -> dict[str, tuple[str, str] | None]:
    """Index declarations by the names a node suffix can match them under.

    A bare name maps to ``None`` once two ecosystems declare it, so an
    ambiguous target stays unlinked rather than resolving to whichever
    manifest was read first. Ecosystem-qualified keys stay resolvable either
    way.

    An absent ecosystem counts as one: the test is ``previous is None``, not
    ``if not previous``, so a name declared once without an ecosystem and once
    with one is ambiguous. The CRUD writer builds its id map from this index,
    so relaxing that here changes what gets stored.
    """
    index: dict[str, tuple[str, str] | None] = {}
    ecosystem_of: dict[str, str] = {}
    for declaration in declarations:
        name = field(declaration, "name", "") or ""
        ecosystem = field(declaration, "ecosystem", "") or ""
        if not name:
            continue
        identity = (ecosystem, name)
        if ecosystem:
            index.setdefault(package_key(ecosystem, name), identity)
        for key in _index_names(name, ecosystem):
            previous = ecosystem_of.get(key)
            if previous is None:
                ecosystem_of[key] = ecosystem
                index[key] = identity
            elif previous != ecosystem:
                index[key] = None
    return index


def resolve_declaration(
    node_id: str,
    index: dict[str, tuple[str, str] | None],
) -> tuple[str, str] | None:
    """The package one external node stands for, or ``None`` if unresolved.

    The first candidate the index knows wins even when it is ambiguous; falling
    through to a broader candidate would resolve exactly the collision the
    ambiguous entry exists to refuse.
    """
    if not node_id.startswith(EXTERNAL_NODE_PREFIX):
        return None
    suffix = node_id[len(EXTERNAL_NODE_PREFIX) :]
    for candidate in declaration_name_candidates(suffix):
        if candidate in index:
            return index[candidate]
    return None


def build_declaration_links(
    declarations: Iterable[Any],
    external_nodes: Iterable[Any],
) -> list[ExternalSystemLink]:
    """Link every external graph node that resolves to a declared package.

    Unresolved and ambiguous nodes are omitted rather than carried as holes,
    which is the same set the stored ``external_system_id`` leaves null.

    The folds this feeds also take import edges, and expect them already
    narrowed to :data:`~.records.IMPORT_EDGE_TYPE` — an unfiltered edge list
    counts calls as package usage.
    """
    index = build_declaration_index(declarations)
    links: list[ExternalSystemLink] = []
    for node in external_nodes:
        node_id = field(node, "node_id", "") or ""
        identity = resolve_declaration(node_id, index)
        if identity is None:
            continue
        ecosystem, name = identity
        links.append(ExternalSystemLink(node_id=node_id, ecosystem=ecosystem, name=name))
    return links


__all__ = [
    "EXTERNAL_NODE_PREFIX",
    "ExternalSystemLink",
    "build_declaration_index",
    "build_declaration_links",
    "declaration_name_candidates",
    "resolve_declaration",
]
