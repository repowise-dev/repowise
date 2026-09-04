"""The declaration-to-external-node match, as a fold over plain records.

The folds in :mod:`.summary` and :mod:`.relationships` take links as given, but
only the persistence layer knew how to build one, so a consumer holding
declarations and graph nodes could not reach them without a database. The
matcher lives here, framework-free, and the CRUD writer calls it too, so the
stored ``external_system_id`` and a link built from plain records cannot
disagree about what resolves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .records import field, package_key

#: Graph nodes standing for a third-party package carry this ``node_id`` prefix.
EXTERNAL_NODE_PREFIX = "external:"


@dataclass(frozen=True, slots=True)
class ExternalSystemLink:
    """One external graph node resolved to the package a manifest declared."""

    node_id: str
    ecosystem: str
    name: str

    def as_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "ecosystem": self.ecosystem, "name": self.name}


def declaration_name_candidates(external_name: str) -> tuple[str, ...]:
    """Declaration names to try for one node suffix, most precise first.

    Resolver output is ecosystem-shaped (npm subpaths, Rust ``::`` paths,
    Python dotted modules), so the declared package is a prefix of the node
    rather than the whole of it.
    """
    candidates = [external_name]
    if external_name.startswith("@"):
        parts = external_name.split("/")
        if len(parts) >= 2:
            candidates.append("/".join(parts[:2]))
    for separator in ("::", ".", "/"):
        if separator in external_name:
            candidates.append(external_name.split(separator, 1)[0])
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


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
        previous = ecosystem_of.get(name)
        if previous is None:
            ecosystem_of[name] = ecosystem
            index[name] = identity
        elif previous != ecosystem:
            index[name] = None
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
