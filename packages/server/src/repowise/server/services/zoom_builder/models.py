"""Plain dataclasses produced by the zoom-map builder.

Framework-agnostic (no Pydantic, no SQLAlchemy) so the pure tree / scoring /
layout / relation functions unit-test without a session, mirroring
``c4_builder.models``. The router wraps these into Pydantic response models.

The zoom map is one nested containment tree:

    system -> layer -> sub-group -> folder(s) -> file

Every node carries an execution-aware ``importance`` (0..1) and a
``sibling_rank`` so the renderer can cap how many children it draws at each
zoom depth, plus a deterministic ``layout`` rect (parent ``[0,1]`` space) for
the clip-and-scale canvas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Node kinds, coarsest to finest. A leaf is always ``file``.
NODE_KINDS = ("system", "layer", "group", "folder", "file")


@dataclass(frozen=True)
class ZoomRect:
    """A child's allocation inside its parent, in parent ``[0,1]`` space.

    The renderer maps this straight to screen space: scale child content by
    ``(w, h)`` and translate by ``(x, y)`` (clip-and-scale recursion).
    """

    x: float
    y: float
    w: float
    h: float


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
    layout: ZoomRect | None = None
    summary: str = ""
    language: str | None = None
    # File-leaf flags (False / defaults on non-file nodes).
    is_entry_point: bool = False
    is_hotspot: bool = False
    is_dead: bool = False
    is_test: bool = False
    on_flow: bool = False         # reachable from an entry point along import edges


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
class ZoomMap:
    """The complete zoom map for one repository at a given depth/focus."""

    root_id: str
    nodes: dict[str, ZoomNode]
    relations: tuple[ZoomRelation, ...]
    project_name: str
    total_files: int
    max_depth: int                # deepest level present in this (possibly pruned) map
    truncated: bool = False       # True when depth/focus pruning dropped deeper nodes
