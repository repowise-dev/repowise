"""Same-named members of unrelated classes in a member's definition file.

In the file that defines ``Client._init_transport``, the word scan also hits
``AsyncClient._init_transport``: its definition and the ``self.`` calls in
its class. Neither is the symbol when the two classes share no inheritance,
so the edit-set scan leaves them out (:class:`_SiblingMember` in
``_edit_sites``). Anything uncertain counts as related and is kept.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.ingestion.symbol_identity import file_of_symbol, id_segment_name
from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server._helpers import node_is_excluded

# ``method_implements`` joins a Go type to an interface its method set satisfies.
_HERITAGE = ("extends", "implements", "method_implements")
# Inheritance levels walked each way; a chain still going past it reads as related.
_MAX_DEPTH = 8
# Kinds whose span is a class body another class can nest in.
_CLASS_KINDS = ("class", "interface", "struct", "enum", "trait", "record", "object")


class Twin(NamedTuple):
    """Another class's member of the same name."""

    span: tuple[int, int]
    # Its class's span when the member sits inside it (not a Go receiver).
    body: tuple[int, int] | None
    # Start lines of its class and the symbol's, 0 when not indexed here.
    heads: tuple[int, int]
    # Bare names of its class and the symbol's.
    names: tuple[str, str]
    # Whether the index holds a base for its class and the symbol's: a header
    # declaring one the index lacks leaves the inheritance unknown.
    resolved: tuple[bool, bool]


class Twins(NamedTuple):
    own_body: tuple[int, int] | None
    members: tuple[Twin, ...]


async def _walk(
    session: AsyncSession, repo_id: str, cls: str, up: bool, spec: Any = None
) -> set[str] | None:
    """Classes *cls* inherits from (*up*) or that inherit from it; ``None`` past the cap.

    A class in a file *spec* excludes is neither found nor walked through.
    """
    near, far = GraphEdge.source_node_id, GraphEdge.target_node_id
    if not up:
        near, far = far, near
    seen: set[str] = set()
    frontier = {cls}
    for _ in range(_MAX_DEPTH):
        rows = await session.execute(
            select(far).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.edge_type.in_(_HERITAGE),
                near.in_(frontier),
            )
        )
        frontier = {r for (r,) in rows.all() if not node_is_excluded(r, spec)} - seen - {cls}
        if not frontier:
            return seen
        seen |= frontier
    return None


async def _inherited(
    session: AsyncSession, repo_id: str, ancestors: set[str], name: str, kinds: frozenset[str]
) -> bool:
    """Whether an ancestor class defines a member named *name*."""
    if not ancestors:
        return False
    rows = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo_id,
            GraphNode.name == name,
            GraphNode.kind.in_(kinds),
            GraphNode.file_path.in_({file_of_symbol(a) for a in ancestors}),
        )
    )
    return any(_class_of(nid) in ancestors for (nid,) in rows.all())


def _class_of(node_id: str) -> str:
    return node_id.rsplit("::", 1)[0]


def _apart(s: int | None, e: int | None, node: GraphNode) -> bool:
    """Whether span ``s..e`` is known and clear of *node*'s: one nested in it,
    or holding it, is not judged."""
    return bool(s and e) and (e < (node.start_line or 0) or s > (node.end_line or 0))


def _judged(nid: str, s: int | None, e: int | None, node: GraphNode, kin: set[str]) -> bool:
    """Whether a same-named member is another class's, apart from *node*'s span."""
    if nid in kin or nid.count("::") < 2 or _class_of(nid) == _class_of(node.node_id):
        return False
    return _apart(s, e, node)


async def _same_named(
    session: AsyncSession,
    repo_id: str,
    node: GraphNode,
    name: str,
    kin: set[str],
    kinds: frozenset[str],
) -> list[tuple[str, int, int]]:
    """``(class, start, end)`` of other classes' members named *name* in *node*'s file."""
    rows = await session.execute(
        select(GraphNode.node_id, GraphNode.start_line, GraphNode.end_line).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "symbol",
            GraphNode.file_path == node.file_path,
            GraphNode.name == name,
            GraphNode.kind.in_(kinds),
            GraphNode.node_id != node.node_id,
        )
    )
    return [(_class_of(nid), s, e) for nid, s, e in rows.all() if _judged(nid, s, e, node, kin)]


async def _unrelated(
    session: AsyncSession,
    repo_id: str,
    own_cls: str,
    others: list[tuple[str, int, int]],
    name: str,
    kinds: frozenset[str],
    spec: Any = None,
) -> list[tuple[str, int, int]]:
    """*others* whose class neither inherits from *own_cls* nor is inherited by it."""
    ancestors = await _walk(session, repo_id, own_cls, up=True, spec=spec)
    descendants = await _walk(session, repo_id, own_cls, up=False, spec=spec)
    if ancestors is None or descendants is None:
        return []
    # Ceiling: an override of an inherited member shares its edit set with
    # every other override of that base, cousins included, so nothing is
    # judged. Walking the family down from each defining base would narrow it.
    if await _inherited(session, repo_id, ancestors, name, kinds):
        return []
    related = ancestors | descendants
    return [o for o in others if o[0] not in related]


async def _class_spans(session: AsyncSession, repo_id: str, path: str) -> list[tuple[int, int]]:
    """Spans of the classes the index holds in *path*."""
    rows = await session.execute(
        select(GraphNode.start_line, GraphNode.end_line).where(
            GraphNode.repository_id == repo_id,
            GraphNode.file_path == path,
            GraphNode.node_type == "symbol",
            GraphNode.kind.in_(_CLASS_KINDS),
        )
    )
    return [(s, e) for s, e in rows.all() if s and e]


async def _based(
    session: AsyncSession, repo_id: str, classes: set[str], spec: Any = None
) -> set[str]:
    """The *classes* the index joins to a base outside the files *spec* excludes."""
    rows = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.edge_type.in_(_HERITAGE),
            GraphEdge.source_node_id.in_(classes),
        )
    )
    return {src for src, tgt in rows.all() if not node_is_excluded(tgt, spec)}


def _holds_class(body: tuple[int, int], classes: list[tuple[int, int]]) -> bool:
    """Whether a class sits strictly inside *body*: its ``self`` may be another's."""
    return any(body[0] <= s and e <= body[1] and (s, e) != body for s, e in classes)


async def _twins(
    session: AsyncSession,
    repo_id: str,
    node: GraphNode,
    own_cls: str,
    others: list[tuple[str, int, int]],
    spec: Any = None,
) -> Twins:
    """Spans, class bodies and heads of *others* beside the symbol's own class."""
    classes = {own_cls} | {o[0] for o in others}
    rows = await session.execute(
        select(GraphNode.node_id, GraphNode.start_line, GraphNode.end_line).where(
            GraphNode.repository_id == repo_id,
            GraphNode.file_path == node.file_path,
            GraphNode.node_id.in_(classes),
        )
    )
    spans = {nid: (s or 0, e or 0) for nid, s, e in rows.all()}
    nested = await _class_spans(session, repo_id, node.file_path)
    based = await _based(session, repo_id, classes, spec)

    def holding(cls: str, s: int, e: int) -> tuple[int, int] | None:
        b = spans.get(cls)
        return b if b and b[0] <= s and e <= b[1] else None

    def body(cls: str, s: int, e: int) -> tuple[int, int] | None:
        b = holding(cls, s, e)
        return None if b is None or _holds_class(b, nested) else b

    def head(cls: str) -> int:
        return spans.get(cls, (0, 0))[0]

    def bare(cls: str) -> str:
        return id_segment_name(cls.rsplit("::", 1)[-1])

    return Twins(
        holding(own_cls, node.start_line, node.end_line),
        tuple(
            Twin(
                (s, e),
                body(cls, s, e),
                (head(cls), head(own_cls)),
                (bare(cls), bare(own_cls)),
                (cls in based, own_cls in based),
            )
            for cls, s, e in others
        ),
    )


async def sibling_twins(
    session: AsyncSession,
    repo_id: str,
    node: GraphNode,
    name: str,
    kin: set[str],
    dirty: frozenset[str],
    *,
    exclude_spec: Any = None,
) -> Twins | None:
    """Members named *name* in *node*'s file whose class is unrelated to *node*'s.

    Only for a member whose file has no edits since the index (its spans would
    be stale). *kin* are the dispatch bases and overrides of *node*, never judged.
    Heritage rows reaching a file *exclude_spec* matches are not followed.
    """
    from repowise.server.mcp_server._edit_sites import _MEMBER_KINDS

    # A file edited since the index, or a symbol without indexed lines: unknown spans.
    if node.kind not in _MEMBER_KINDS or node.file_path in dirty:
        return None
    # Go has no ``self``: a twin's calls go through receivers no text tells
    # apart, so its definition stays listed beside them.
    if not (node.start_line and node.end_line) or node.file_path.endswith(".go"):
        return None
    own_cls = _class_of(node.node_id)
    others = await _same_named(session, repo_id, node, name, kin, _MEMBER_KINDS)
    if others:
        others = await _unrelated(
            session, repo_id, own_cls, others, name, _MEMBER_KINDS, exclude_spec
        )
    if not others:
        return None
    return await _twins(session, repo_id, node, own_cls, others, exclude_spec)
