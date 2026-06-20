"""TypeScript / JavaScript heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _type_clause(
    clause: Node,
    name: str,
    line: int,
    src: str,
    kind: str,
    keyword: str,
    out: list[HeritageRelation],
) -> None:
    """Emit a *kind* relation for each type listed in an extends/implements clause."""
    for type_node in clause.children:
        if type_node.type in (keyword, ","):
            continue
        parent = node_text(type_node, src).strip()
        if parent:
            out.append(HeritageRelation(child_name=name, parent_name=parent, kind=kind, line=line))


def _class_heritage(
    heritage: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """``class Foo extends Bar implements IFoo, IBar``."""
    for clause in heritage.children:
        if clause.type == "extends_clause":
            _type_clause(clause, name, line, src, "extends", "extends", out)
        elif clause.type == "implements_clause":
            _type_clause(clause, name, line, src, "implements", "implements", out)


def _extract_ts_js_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """TypeScript/JavaScript: class Foo extends Bar implements IFoo, IBar."""
    for child in def_node.children:
        if child.type == "class_heritage":
            _class_heritage(child, name, line, src, out)
        # interface extends: interface Foo extends Bar
        elif child.type == "extends_type_clause":
            _type_clause(child, name, line, src, "extends", "extends", out)
