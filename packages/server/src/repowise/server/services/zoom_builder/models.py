"""Plain dataclasses produced by the zoom-map builder.

Framework-agnostic (no Pydantic, no SQLAlchemy) so the pure tree / scoring /
metric / relation functions unit-test without a session, mirroring
``c4_builder.models``. The router wraps these into Pydantic response models.

The zoom map is one nested containment tree:

    system -> layer -> sub-group -> folder(s) -> file

Every node carries an execution-aware ``importance`` (0..1) and a
``sibling_rank`` so the renderer can cap how many children it draws at each
zoom depth. Placement is not here: the canvas packs each parent's children
itself, from those two fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Node kinds, coarsest to finest. A leaf is always ``file``.
NODE_KINDS = ("system", "layer", "group", "folder", "file")


@dataclass(frozen=True)
class ZoomMetrics:
    """Counts rolled up over a node's subtree (a file is its own subtree)."""

    file_count: int = 0
    descendant_count: int = 0
    hotspot_count: int = 0
    dead_count: int = 0
    entry_point_count: int = 0
    on_flow_count: int = 0


@dataclass(frozen=True)
class ZoomNode:
    """One node in the containment tree."""

    id: str                       # "zm:sys" | "zm:layer:<id>" | "zm:<path>" ...
    parent_id: str | None         # None only for the system root
    level: int                    # 0 system, 1 layer, 2 group, 3+ folder, leaf file
    kind: str                     # one of NODE_KINDS
    name: str                     # display label (may be "a/b" for compressed dirs)
    path: str = ""                # repo-relative path for folder/file nodes; "" otherwise
    children: tuple[str, ...] = field(default_factory=tuple)
    importance: float = 0.0       # 0..1, execution-aware, normalized within siblings
    sibling_rank: int = 0         # 1 = most important among siblings
    metrics: ZoomMetrics = field(default_factory=ZoomMetrics)
    summary: str = ""
    language: str | None = None
    # The module page documenting this folder, when one does. Empty on every
    # other node, and on a folder no page covers.
    page_id: str = ""
    # Code-health score on the 0..10 scale (higher = healthier), matching the
    # /files treemap. A file carries its own defect score (falling back to the
    # overall score); a container carries the loc-weighted mean over its scored
    # descendants. None when nothing in the subtree was scored (health is sparse).
    health_score: float | None = None
    # Lines of code for the file leaf, used only as the health-rollup weight so
    # a container's mean matches the treemap. Not surfaced in the response.
    loc: int = 1
    # File-leaf flags (False / defaults on non-file nodes).
    is_entry_point: bool = False
    is_hotspot: bool = False
    is_dead: bool = False
    is_test: bool = False
    on_flow: bool = False         # reachable from an entry point along import edges
    # Up to three technologies (code languages and imported frameworks) the
    # subtree is written in, most files first. Empty on file leaves, which carry
    # ``language`` instead.
    technologies: tuple[str, ...] = ()
    # The containers (deployables) this subtree's files belong to, most files
    # first. One id means the node lies wholly inside that container.
    container_ids: tuple[str, ...] = ()


def evolve(node: ZoomNode, **changes: object) -> ZoomNode:
    """``dataclasses.replace`` for a ZoomNode, without re-running ``__init__``.

    Every pass over the tree (metrics, health, scoring, annotation) copies all
    of its nodes, 7k on a large repository, and ``replace`` re-runs a 23-field
    frozen ``__init__`` per copy. ZoomNode has no ``__post_init__`` and no
    derived field, so copying the instance dict is the same copy, about four
    times faster. An unknown field name is a bug here, as it is for ``replace``.
    """
    unknown = changes.keys() - node.__dict__.keys()
    if unknown:
        raise TypeError(f"ZoomNode has no field(s) {sorted(unknown)}")
    new = object.__new__(ZoomNode)
    new.__dict__.update(node.__dict__, **changes)
    return new


def ancestry(node_id: str, nodes: dict[str, ZoomNode]) -> list[str]:
    """Root-to-node id path of ``node_id`` in the tree ``nodes``."""
    out: list[str] = []
    cur: str | None = node_id
    while cur is not None:
        out.append(cur)
        nxt = nodes.get(cur)
        if nxt is None:  # defensive: never happens from the builder
            break
        cur = nxt.parent_id
    out.reverse()
    return out


@dataclass(frozen=True)
class ZoomRelation:
    """An aggregated edge between two sibling subtrees under a shared parent.

    The renderer draws these when the viewer is zoomed into ``parent_id``: a
    relation from ``source_id`` to ``target_id`` (both direct children of the
    parent), labelled and coupling-tiered like the C4 relations.
    """

    parent_id: str
    source_id: str
    target_id: str
    label: str = ""
    edge_count: int = 1
    coupling: str = ""            # loose | moderate | tight


@dataclass(frozen=True)
class ZoomExternal:
    """A third-party system the code reaches: a database, an API, a service.

    Drawn beside the tree, not inside it; ``ZoomMap.external_relations`` link
    tree nodes to it at every zoom level.
    """

    id: str                       # "ext:<package name>"
    name: str                     # the declared package name
    display_name: str
    kind: str                     # io_kind when typed (db | network), else category
    category: str                 # service | framework | library | tool
    io_kind: str | None
    ecosystem: str                # pypi | npm | cargo | ...
    # Repository-wide, not narrowed by focus or max_depth.
    file_count: int = 0           # distinct production files importing it
    edge_count: int = 0           # import edges reaching it


@dataclass(frozen=True)
class ZoomContainer:
    """A deployable unit (C4 container): a package-manifest root."""

    id: str                       # "pkg:<root path>" ("pkg:." for the repo root)
    name: str                     # manifest package name, else the root path
    path: str                     # repo-relative root; "" for the repo root
    kind: str                     # extension | web_app | api | cli | worker | library
    technologies: tuple[str, ...] = ()
    file_count: int = 0           # its files on the whole tree, not narrowed by focus


@dataclass(frozen=True)
class ZoomMap:
    """The complete zoom map for one repository at a given depth/focus."""

    root_id: str
    nodes: dict[str, ZoomNode]
    relations: tuple[ZoomRelation, ...]
    project_name: str
    total_files: int
    max_depth: int                # deepest level present in this (possibly pruned) map
    truncated: bool = False       # True when depth/focus pruning dropped deeper nodes
    # Files the view knows about that curation assigned to no layer, so they are
    # in no tree at all. ``total_files`` stays the count actually placed; this is
    # the rest of the denominator, so a map can say what it is not showing.
    unclaimed_files: int = 0
    externals: tuple[ZoomExternal, ...] = ()
    # Same shape as ``relations`` except ``target_id`` is a ``ZoomExternal`` id:
    # ``source_id`` is a child of ``parent_id`` whose subtree imports it.
    external_relations: tuple[ZoomRelation, ...] = ()
    containers: tuple[ZoomContainer, ...] = ()
