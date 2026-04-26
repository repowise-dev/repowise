"""Python heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_python_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Python: class Foo(Bar, Baz, metaclass=Meta)."""
    superclasses = def_node.child_by_field_name("superclasses")
    if superclasses is None:
        for child in def_node.children:
            if child.type == "argument_list":
                superclasses = child
                break
    if superclasses is None:
        return

    for child in superclasses.children:
        if child.type in ("(", ")", ","):
            continue
        if child.type == "keyword_argument":
            continue
        parent = node_text(child, src).strip()
        if parent:
            bare = parent.split(".")[-1]
            out.append(
                HeritageRelation(child_name=name, parent_name=bare, kind="extends", line=line)
            )
