"""Java heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _superclass(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """``class Foo extends Bar``."""
    superclass = def_node.child_by_field_name("superclass")
    if not superclass:
        return
    parent = node_text(superclass, src).strip().removeprefix("extends").strip()
    if parent:
        out.append(
            HeritageRelation(
                child_name=name,
                parent_name=parent.split(".")[-1],
                kind="extends",
                line=line,
            )
        )


def _permits(def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]) -> None:
    """``sealed class Foo permits A, B``: emit a parent -> child ``extends`` edge.

    The permits clause is the inverse of each child's own ``extends Foo``, so
    emitting it lets the parent file reach every permitted subclass even when
    no other code references those subclasses directly.
    """
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
                if permit_name:
                    out.append(
                        HeritageRelation(
                            child_name=permit_name,
                            parent_name=name,
                            kind="extends",
                            line=line,
                        )
                    )


def _interfaces_node(def_node: Node) -> Node | None:
    """The interface list: ``interfaces`` field, or ``extends_interfaces`` for interfaces."""
    interfaces = def_node.child_by_field_name("interfaces")
    if interfaces is not None:
        return interfaces
    for child in def_node.children:
        if child.type == "extends_interfaces":
            return child
    return None


def _interfaces(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """``class Foo implements IFoo, IBar`` / ``interface Foo extends IBar``."""
    interfaces = _interfaces_node(def_node)
    if interfaces is None:
        return
    kind = "implements" if def_node.type == "class_declaration" else "extends"
    for child in interfaces.children:
        if child.type in ("implements", "extends", ",", "type_list"):
            if child.type == "type_list":
                for type_node in child.children:
                    if type_node.type == ",":
                        continue
                    parent = node_text(type_node, src).strip().split(".")[-1]
                    if parent:
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
            out.append(HeritageRelation(child_name=name, parent_name=parent, kind=kind, line=line))


def _extract_java_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Java: class Foo extends Bar implements IFoo, IBar."""
    _superclass(def_node, name, line, src, out)
    _permits(def_node, name, line, src, out)
    _interfaces(def_node, name, line, src, out)
