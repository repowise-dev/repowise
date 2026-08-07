"""Pascal heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text

_CLASS_LIKE_KINDS = {"declClass", "declIntf", "declHelper"}


def _extract_pascal_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Pascal: ``TFoo = class(TBar, IBaz)`` / ``IFoo = interface(IBar)`` /
    ``TFooHelper = class helper(TBaseHelper) for TFoo``.

    ``def_node`` is the outer ``declType`` node (``TFoo = ...;``), not the
    ``declClass``/``declIntf``/``declHelper`` node directly -- see the note
    in ``languages/specs/pascal.py`` on why ``heritage_node_types`` points
    at ``declType``. Drill into declType's `type` field first to reach the
    actual class/interface/helper body.

    Pascal's grammar puts the base class and every implemented interface in
    the *same* comma-separated `parent` list -- `class(TBase, IFoo, IBar)`
    -- with no syntax distinguishing "the one base class" from "the
    interfaces". By (near-universal) convention the first entry is the base
    class and the rest are interfaces; nothing in the grammar enforces that
    ordering, so this is a best-effort heuristic, not a guarantee.
    """
    type_children = def_node.children_by_field_name("type")
    if not type_children:
        return
    type_node = type_children[0]
    if type_node.type not in _CLASS_LIKE_KINDS:
        return

    parent_nodes = type_node.children_by_field_name("parent")
    parents = [node_text(p, src).strip() for p in parent_nodes if p.type == "typeref"]
    parents = [p for p in parents if p and p != name]

    for i, parent in enumerate(parents):
        out.append(
            HeritageRelation(
                child_name=name,
                parent_name=parent,
                kind="extends" if i == 0 else "implements",
                line=line,
            )
        )

    if type_node.type == "declHelper":
        # The type being extended (`for TFoo`) is a distinct relationship
        # from the helper's own ancestor helper (`parent`, handled above).
        # It carries no field name in the grammar (`$.kFor, $.typeref` with
        # no `field()` wrapper), so it's located positionally: the last
        # named `typeref` child not already consumed as `parent`.
        parent_typeref_ids = {p.id for p in parent_nodes}
        for child in type_node.named_children:
            if child.type == "typeref" and child.id not in parent_typeref_ids:
                extended = node_text(child, src).strip()
                if extended and extended != name:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=extended,
                            kind="extends",
                            line=line,
                        )
                    )
