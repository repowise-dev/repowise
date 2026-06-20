"""PHP heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _named_children_text(node: Node, src: str, name: str) -> list[tuple[str, Node]]:
    """Return ``(text, child)`` for each ``name`` child whose text differs from *name*."""
    out: list[tuple[str, Node]] = []
    for sub in node.children:
        if sub.type == "name":
            parent = node_text(sub, src).strip()
            if parent and parent != name:
                out.append((parent, sub))
    return out


def _base_clause(clause: Node, name: str, line: int, src: str, out: list[HeritageRelation]) -> None:
    """``class Foo extends Bar``."""
    for parent, _ in _named_children_text(clause, src, name):
        out.append(HeritageRelation(child_name=name, parent_name=parent, kind="extends", line=line))


def _interface_clause(
    clause: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """``class Foo implements IFoo, IBar``."""
    for parent, _ in _named_children_text(clause, src, name):
        out.append(
            HeritageRelation(child_name=name, parent_name=parent, kind="implements", line=line)
        )


def _trait_uses(body: Node, name: str, src: str, out: list[HeritageRelation]) -> None:
    """``use TraitName;`` statements inside the class body."""
    for stmt in body.children:
        if stmt.type != "use_declaration":
            continue
        for trait_name, _ in _named_children_text(stmt, src, name):
            out.append(
                HeritageRelation(
                    child_name=name,
                    parent_name=trait_name,
                    kind="mixin",
                    line=stmt.start_point[0] + 1,
                )
            )


def _extract_php_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """PHP: ``class Foo extends Bar implements IFoo, IBar; use TraitName;``."""
    for child in def_node.children:
        if child.type == "base_clause":
            _base_clause(child, name, line, src, out)
        elif child.type == "class_interface_clause":
            _interface_clause(child, name, line, src, out)
        elif child.type == "declaration_list":
            _trait_uses(child, name, src, out)
