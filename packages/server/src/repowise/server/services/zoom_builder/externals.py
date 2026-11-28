"""External systems on the zoom map: which tree files import which declared package.

The architecture view already carries everything this needs: the ``external:*``
import targets are file-typed graph nodes, so their import edges survive into
``view.edges``, and ``view.external_systems`` holds the declared packages. The
node-to-package match is core's :func:`build_declaration_links`, the same fold
the CRUD writer uses for the stored ``external_system_id``, so no extra query
runs and a hosted caller holding only the view gets the same answer.

Two outputs come from that one resolution. Frameworks become technologies on
the cards (``technology.py``). Runtime systems (a service, or a database or
network client) become ``ZoomExternal`` cards beside the tree, linked to every
tree node whose subtree imports them, one link per zoom level, the way
``relations.py`` attributes file edges to sibling pairs.

Pure: plain records in, frozen dataclasses out.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from typing import Any

from repowise.core.analysis.c4.labels import coupling_strength, relation_label
from repowise.core.analysis.external_systems import (
    EXTERNAL_NODE_PREFIX,
    IMPORT_EDGE_TYPE,
    build_declaration_links,
)
from repowise.core.support_paths import is_config_path

from .models import ZoomExternal, ZoomNode, ZoomRelation, ancestry
from .tree import file_id

#: Boundary types that make a declared package a system the code talks to,
#: whatever its category says (an HTTP client is a "library" that reaches a
#: remote API).
_SYSTEM_IO_KINDS = frozenset({"db", "network"})

#: One ``(file path, external system view, file is production)`` per import edge.
Imports = list[tuple[str, Any, bool]]


def is_production(node: ZoomNode) -> bool:
    """Whether a file's imports count as the system's own: not a test, not config.

    The exclusion the C4 roll-up applies, read off the ``is_test`` flag
    ingestion stored rather than re-classifying every path per request, so a
    test importing a database driver does not draw the database into the map.
    """
    return not (node.is_test or is_config_path(node.path))


def resolve_imports(
    edges: Iterable[tuple[str, str, str]],
    files: Mapping[str, ZoomNode],
    declared: Sequence[Any],
) -> Imports:
    """Import edges from a tree file (``files`` maps its path to its leaf) to an
    ``external:*`` node, resolved to the declared package it stands for.
    Unresolved targets are dropped."""
    raw = [
        (src, tgt)
        for src, tgt, etype in edges
        if etype == IMPORT_EDGE_TYPE and src in files and tgt.startswith(EXTERNAL_NODE_PREFIX)
    ]
    if not raw or not declared:
        return []
    links = build_declaration_links(declared, ({"node_id": t} for t in {t for _, t in raw}))
    by_name = {d.name: d for d in declared}
    package = {link.node_id: by_name[link.name] for link in links if link.name in by_name}
    production = {path: is_production(files[path]) for path in {src for src, _ in raw}}
    return [(src, package[tgt], production[src]) for src, tgt in raw if tgt in package]


def is_system(ext: Any) -> bool:
    """A runtime system worth a card, as opposed to a framework or library."""
    return ext.category == "service" or ext.io_kind in _SYSTEM_IO_KINDS


def build_externals(
    nodes: dict[str, ZoomNode], imports: Imports
) -> tuple[tuple[ZoomExternal, ...], tuple[ZoomRelation, ...]]:
    """The external-system cards and their links into the tree at every level."""
    edge_counts: dict[str, int] = defaultdict(int)
    files_by_ext: dict[str, set[str]] = defaultdict(set)
    views: dict[str, Any] = {}
    links: dict[tuple[str, str, str], int] = defaultdict(int)
    chains: dict[str, list[str]] = {}

    for path, ext, production in imports:
        if not (production and is_system(ext)):
            continue
        views[ext.id] = ext
        edge_counts[ext.id] += 1
        files_by_ext[ext.id].add(path)
        fid = file_id(path)
        chain = chains.get(fid) or chains.setdefault(fid, ancestry(fid, nodes))
        for parent, child in pairwise(chain):
            links[(parent, child, ext.id)] += 1

    externals = tuple(
        ZoomExternal(
            id=ext.id,
            name=ext.name,
            display_name=ext.display_name or ext.name,
            kind=ext.io_kind or ext.category,
            category=ext.category,
            io_kind=ext.io_kind,
            ecosystem=ext.ecosystem,
            file_count=len(files_by_ext[ext.id]),
            edge_count=edge_counts[ext.id],
        )
        for ext in sorted(views.values(), key=lambda e: (-edge_counts[e.id], e.id))
    )
    label = relation_label((IMPORT_EDGE_TYPE,))
    relations = tuple(
        sorted(
            (
                ZoomRelation(
                    parent_id=parent,
                    source_id=child,
                    target_id=ext_id,
                    label=label,
                    edge_count=count,
                    coupling=coupling_strength(count),
                )
                for (parent, child, ext_id), count in links.items()
            ),
            key=lambda r: (r.parent_id, -r.edge_count, r.source_id, r.target_id),
        )
    )
    return externals, relations
