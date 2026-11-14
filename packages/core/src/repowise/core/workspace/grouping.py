"""Service groups: fold a large estate's services into a few named systems.

A map of 300 services is a wall of cards. Grouping gives it a first level to
read: each service joins at most one group, found by three sources in priority
order, each claiming only what the earlier ones left:

1. **tag**: the repo declares a ``group:<name>`` tag in
   ``.repowise-workspace.yaml``. Other tags (``backend``, ``team:x``) serve
   conformance rules and never make a group.
2. **name**: a shared leading name of repo aliases (``ordering.api``,
   ``ordering.worker`` -> ``ordering``), kept only when it spans 2+ repos and
   under half the estate's repos. A prefix that covers half the estate is an
   organisation prefix, not a system, so the family is split on its next name
   token instead (``acme-billing-*`` inside ``acme-*``), up to
   :data:`MAX_NAME_DEPTH` tokens.
3. **linked**: community detection over runtime and data edges among the
   services still ungrouped, labelled by the member most services call. Its
   id comes from the smallest member id, so a new caller can relabel a linked
   group but never renames it.

A group is kept only with 2+ members; a lone service falls through to the next
source and may end ungrouped. Groups are emitted only when they reduce
something: more than :data:`MIN_SERVICES_TO_GROUP` services and at least two
groups. Below that every service is drawn on its own and no node carries a
group.

Pure and deterministic: inputs are walked in sorted order and the community
detection is seeded, so the same graph always yields the same groups. It reads
only node and edge attributes, so a caller holding a stored ``system_graph.json``
can run it through :meth:`SystemGraph.from_dict`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repowise.core.workspace.system_graph import SystemEdge, SystemNode

_log = logging.getLogger("repowise.workspace.grouping")

#: Groups appear only above this many services; a smaller map reads fine flat.
#: This gate and :data:`MIN_GROUPS` live only here: a view treats a non-empty
#: ``groups`` list as the answer and never re-derives it.
MIN_SERVICES_TO_GROUP = 40

#: ...and only when at least this many groups (each 2+ members) form.
MIN_GROUPS = 2

#: A name family must stay under this share of the estate's repos.
MAX_NAME_FAMILY_SHARE = 0.5

#: Deepest alias prefix a name family may use (``a``, ``a-b``, ``a-b-c``).
MAX_NAME_DEPTH = 3

#: Edge families (``SystemEdge.family``) the linkage source clusters on: what
#: services call or share at run time. Build edges fan into library hubs and
#: would pull every service toward the shared libraries; history (co-change)
#: is correlation, not a dependency.
LINKAGE_FAMILIES: frozenset[str] = frozenset({"runtime", "data"})

#: Linkage is skipped (those services stay ungrouped) past this many linked
#: service pairs. Louvain is near-linear in edges; at this size it runs well
#: under a second. Ceiling: an estate past it needs a declared ``group:`` tag
#: or a faster partitioner.
MAX_LINKAGE_PAIRS = 50_000

#: Fixed seed for the community detection, the same one file communities use.
LINKAGE_SEED = 42

_GROUP_TAG_PREFIX = "group:"

#: A group a source found: ``(group id, label, members)``.
_Found = tuple[str, str, list["SystemNode"]]
_TOKEN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class ServiceGroup:
    """One group of services: what the map draws as a single card."""

    #: ``tag:<name>``, ``name:<prefix>`` or ``linked:<smallest member id>``.
    id: str
    label: str
    source: str  # tag | name | linked
    member_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "source": self.source,
            "member_count": self.member_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServiceGroup:
        return cls(
            id=data["id"],
            label=data.get("label", data["id"]),
            source=data.get("source", ""),
            member_count=data.get("member_count", 0),
        )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def declared_group(tags: Iterable[str]) -> str | None:
    """The repo's declared service group: its first non-empty ``group:<name>`` tag.

    The prefix matches in any case; the name keeps its own, as conformance's
    ``tag:<name>`` matcher does. The one reader of the ``group:`` convention.
    """
    for tag in (t.strip() for t in tags if t):
        name = tag[len(_GROUP_TAG_PREFIX) :].strip()
        if tag.lower().startswith(_GROUP_TAG_PREFIX) and name:
            return name
    return None


def _by_tag(nodes: list[SystemNode], repo_tags: Mapping[str, Iterable[str]]) -> list[_Found]:
    """Declared groups of 2+ services."""
    groups: dict[str, list[SystemNode]] = {}
    for node in nodes:
        name = declared_group(repo_tags.get(node.repo, ()))
        if name is not None:
            groups.setdefault(name, []).append(node)
    return [(f"tag:{name}", name, m) for name, m in sorted(groups.items()) if len(m) >= 2]


def _alias_prefix(alias: str, depth: int) -> tuple[str, str] | None:
    """``(key, label)`` for the first *depth* name tokens of *alias*.

    None when the alias has fewer tokens. An alias of exactly *depth* tokens
    joins the family its whole name starts (``ordering`` with ``ordering.api``).
    """
    tokens = list(_TOKEN.finditer(alias))
    if len(tokens) < depth:
        return None
    key = ".".join(t.group().lower() for t in tokens[:depth])
    return key, alias[tokens[0].start() : tokens[depth - 1].end()]


def _families_at(
    pool: list[SystemNode], depth: int
) -> dict[str, tuple[str, list[SystemNode]]]:
    """Split *pool* by the first *depth* alias tokens: ``{key: (label, members)}``."""
    families: dict[str, tuple[str, list[SystemNode]]] = {}
    for node in pool:
        prefix = _alias_prefix(node.repo, depth)
        if prefix is not None:
            families.setdefault(prefix[0], (prefix[1], []))[1].append(node)
    return families


def _by_name(nodes: list[SystemNode], estate_repos: int) -> list[_Found]:
    """Name families of 2+ repos and under half the estate, widest prefix first."""
    max_repos = estate_repos * MAX_NAME_FAMILY_SHARE
    found: dict[str, tuple[str, list[SystemNode]]] = {}
    pending: list[list[SystemNode]] = [nodes]
    for depth in range(1, MAX_NAME_DEPTH + 1):
        families = [f for pool in pending for f in _families_at(pool, depth).items()]
        too_wide: list[list[SystemNode]] = []
        for key, (label, members) in families:
            repos = len({m.repo for m in members})
            if 2 <= repos < max_repos:
                found[key] = (label, members)
            elif repos >= 2:
                too_wide.append(members)
        pending = too_wide
    return [(f"name:{key}", label, m) for key, (label, m) in sorted(found.items())]


def _linkage_pairs(ids: set[str], edges: list[SystemEdge]) -> dict[tuple[str, str], int]:
    """Summed edge weight per unordered pair of *ids* over :data:`LINKAGE_FAMILIES`."""
    pair_weight: dict[tuple[str, str], int] = {}
    for edge in edges:
        if edge.family not in LINKAGE_FAMILIES or edge.source == edge.target:
            continue
        if edge.source in ids and edge.target in ids:
            pair = (min(edge.source, edge.target), max(edge.source, edge.target))
            pair_weight[pair] = pair_weight.get(pair, 0) + max(edge.weight, 1)
    return pair_weight


def _communities(pair_weight: dict[tuple[str, str], int]) -> list[set[str]]:
    """Seeded Louvain over the weighted pairs.

    Louvain because it needs no target group count, is near-linear, ships with
    networkx (a core dependency, unlike Leiden's graspologic, which would make
    the result depend on what is installed), and is deterministic under a fixed
    seed and a fixed insertion order. A networkx upgrade may still move a
    borderline service between linked groups; tag and name groups never move.
    """
    import networkx as nx  # deferred: only estates past the size gate pay the import

    graph = nx.Graph()
    graph.add_nodes_from(sorted({n for pair in pair_weight for n in pair}))
    graph.add_weighted_edges_from((a, b, w) for (a, b), w in sorted(pair_weight.items()))
    return nx.community.louvain_communities(graph, weight="weight", seed=LINKAGE_SEED)


def _by_linkage(nodes: list[SystemNode], edges: list[SystemEdge]) -> list[_Found]:
    """Linked communities of 2+ services.

    Each is labelled by its most-called member, and its id names its smallest
    member id: the label may move as callers change, the id only when the
    smallest member leaves the community.
    """
    by_id = {n.id: n for n in nodes}
    pair_weight = _linkage_pairs(set(by_id), edges)
    if len(pair_weight) > MAX_LINKAGE_PAIRS:
        _log.info(
            "Skipping linkage grouping: %d linked service pairs exceeds the %d cap",
            len(pair_weight),
            MAX_LINKAGE_PAIRS,
        )
        return []
    communities = [c for c in _communities(pair_weight) if len(c) >= 2] if pair_weight else []
    fan_in = _fan_in(edges) if communities else {}
    found: list[_Found] = []
    for community in communities:
        members = [by_id[nid] for nid in sorted(community)]
        anchor = min(members, key=lambda m: (-fan_in.get(m.id, 0), m.name, m.id))
        found.append((f"linked:{members[0].id}", anchor.name, members))
    return found


def _fan_in(edges: list[SystemEdge]) -> dict[str, int]:
    """How many distinct services call each service over :data:`LINKAGE_FAMILIES`."""
    callers: dict[str, set[str]] = {}
    for edge in edges:
        if edge.family in LINKAGE_FAMILIES and edge.source != edge.target:
            callers.setdefault(edge.target, set()).add(edge.source)
    return {nid: len(sources) for nid, sources in callers.items()}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def group_services(
    nodes: list[SystemNode],
    edges: list[SystemEdge],
    repo_tags: Mapping[str, Iterable[str]] | None = None,
) -> tuple[dict[str, str], list[ServiceGroup]]:
    """Assign services to groups: ``({node_id: group_id}, groups)``.

    Both are empty when the estate is at or under
    :data:`MIN_SERVICES_TO_GROUP` services or fewer than :data:`MIN_GROUPS`
    groups form. *repo_tags* maps a repo alias to its declared tags.
    """
    if len(nodes) <= MIN_SERVICES_TO_GROUP:
        return {}, []

    remaining = sorted(nodes, key=lambda n: n.id)
    estate_repos = len({n.repo for n in remaining})
    sources: tuple[tuple[str, Callable[[list[SystemNode]], list[_Found]]], ...] = (
        ("tag", lambda pool: _by_tag(pool, repo_tags or {})),
        ("name", lambda pool: _by_name(pool, estate_repos)),
        ("linked", lambda pool: _by_linkage(pool, edges)),
    )
    groups: list[ServiceGroup] = []
    assignment: dict[str, str] = {}
    for source, find in sources:
        for group_id, label, members in find(remaining):
            groups.append(ServiceGroup(group_id, label, source, len(members)))
            assignment.update((m.id, group_id) for m in members)
        remaining = [n for n in remaining if n.id not in assignment]

    if len(groups) < MIN_GROUPS:
        return {}, []
    return assignment, sorted(groups, key=lambda g: (-g.member_count, g.id))
