"""Swift heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_swift_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Swift: ``class Foo: Bar, Protocol1`` — inheritance via ``:`` separator."""
    for child in def_node.children:
        if child.type == "inheritance_specifier":
            for type_child in child.children:
                if type_child.type == "user_type":
                    for id_node in type_child.children:
                        if id_node.type == "type_identifier":
                            parent = node_text(id_node, src).strip()
                            if parent and parent != name:
                                out.append(
                                    HeritageRelation(
                                        child_name=name,
                                        parent_name=parent,
                                        kind="extends",
                                        line=line,
                                    )
                                )
