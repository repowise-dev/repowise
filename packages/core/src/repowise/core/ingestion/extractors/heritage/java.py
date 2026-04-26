"""Java heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_java_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Java: class Foo extends Bar implements IFoo, IBar."""
    superclass = def_node.child_by_field_name("superclass")
    if superclass:
        parent = node_text(superclass, src).strip()
        parent = parent.removeprefix("extends").strip()
        if parent:
            out.append(
                HeritageRelation(
                    child_name=name,
                    parent_name=parent.split(".")[-1],
                    kind="extends",
                    line=line,
                )
            )

    interfaces = def_node.child_by_field_name("interfaces")
    if interfaces:
        for child in interfaces.children:
            if child.type in ("implements", "extends", ",", "type_list"):
                if child.type == "type_list":
                    for type_node in child.children:
                        if type_node.type != ",":
                            parent = node_text(type_node, src).strip().split(".")[-1]
                            if parent:
                                kind = (
                                    "implements"
                                    if def_node.type == "class_declaration"
                                    else "extends"
                                )
                                out.append(
                                    HeritageRelation(
                                        child_name=name,
                                        parent_name=parent,
                                        kind=kind,
                                        line=line,
                                    )
                                )
                continue
            parent = node_text(child, src).strip().split(".")[-1]
            if parent and parent not in ("implements", "extends"):
                kind = "implements" if def_node.type == "class_declaration" else "extends"
                out.append(
                    HeritageRelation(
                        child_name=name,
                        parent_name=parent,
                        kind=kind,
                        line=line,
                    )
                )
