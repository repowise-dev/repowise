"""Pascal heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ...type_names import bare_type_name
from ..helpers import node_text

_CLASS_LIKE_KINDS = {"declClass", "declIntf", "declHelper"}


def _extract_parents(type_node: Node, name: str, src: str) -> list[str]:
    """The comma-separated ``parent`` list -- `class(TBase, IFoo, IBar)` --
    minus blanks and self-references.

    Pascal's grammar puts the base class and every implemented interface in
    the *same* list, with no syntax distinguishing "the one base class"
    from "the interfaces". By (near-universal) convention the first entry
    is the base class and the rest are interfaces; nothing in the grammar
    enforces that ordering, so the caller's "first = extends, rest =
    implements" split is a best-effort heuristic, not a guarantee.
    """
    parent_nodes = type_node.children_by_field_name("parent")
    parents = [bare_type_name(node_text(p, src)) for p in parent_nodes if p.type == "typeref"]
    return [p for p in parents if p and p != name]


def _extract_helper_extended_types(type_node: Node, name: str, src: str) -> list[str]:
    """The type(s) a `class helper` extends -- `class helper(...) for TFoo`.

    This is a distinct relationship from the helper's own ancestor helper
    (the `parent` list, handled by ``_extract_parents``). It carries no
    field name in the grammar (`$.kFor, $.typeref` with no `field()`
    wrapper), so it's located positionally: every named `typeref` child not
    already consumed as a `parent`.
    """
    parent_ids = {p.id for p in type_node.children_by_field_name("parent")}
    extended: list[str] = []
    for child in type_node.named_children:
        if child.type != "typeref" or child.id in parent_ids:
            continue
        text = bare_type_name(node_text(child, src))
        if text and text != name:
            extended.append(text)
    return extended


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
    """
    type_children = def_node.children_by_field_name("type")
    if not type_children:
        return
    type_node = type_children[0]
    if type_node.type not in _CLASS_LIKE_KINDS:
        return

    for i, parent in enumerate(_extract_parents(type_node, name, src)):
        out.append(
            HeritageRelation(
                child_name=name,
                parent_name=parent,
                kind="extends" if i == 0 else "implements",
                line=line,
            )
        )

    if type_node.type == "declHelper":
        for extended in _extract_helper_extended_types(type_node, name, src):
            out.append(
                HeritageRelation(child_name=name, parent_name=extended, kind="extends", line=line)
            )
