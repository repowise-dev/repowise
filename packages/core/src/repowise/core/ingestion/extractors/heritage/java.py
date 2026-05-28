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

    # `sealed class Foo permits A, B {}` — emit a permits edge in BOTH
    # directions so the analyzer treats the sealed hierarchy as a closed
    # set. Parent → child = "extends" (the permits clause is the
    # inverse of the children's own ``extends Foo``), letting the parent
    # file reach every permitted subclass even when no other code references
    # those subclasses directly.
    for child in def_node.children:
        if child.type != "permits":
            continue
        for sub in child.children:
            if sub.type != "type_list":
                continue
            for type_node in sub.children:
                if type_node.type in (",", "permits"):
                    continue
                permit_name = node_text(type_node, src).strip().split(".")[-1]
                if not permit_name:
                    continue
                out.append(
                    HeritageRelation(
                        child_name=permit_name,
                        parent_name=name,
                        kind="extends",
                        line=line,
                    )
                )

    interfaces = def_node.child_by_field_name("interfaces")
    # Java interface declarations use `extends_interfaces` for the parent list.
    if interfaces is None:
        for child in def_node.children:
            if child.type == "extends_interfaces":
                interfaces = child
                break
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
