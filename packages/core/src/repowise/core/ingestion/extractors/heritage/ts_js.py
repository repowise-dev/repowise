"""TypeScript / JavaScript heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ...type_names import bare_type_name
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
        parent = bare_type_name(node_text(type_node, src))
        if parent:
            out.append(HeritageRelation(child_name=name, parent_name=parent, kind=kind, line=line))


_CLAUSE_KINDS = {"extends_clause": "extends", "implements_clause": "implements"}

# What a base may be spelled as when it sits directly under the heritage node.
# `extends` takes any expression there, but only these two can name a class;
# anything else is the grammar reading something it does not understand, which
# is what a type annotation in a .js file looks like to it.
_UNWRAPPED_BASE_TYPES = frozenset({"identifier", "member_expression"})


def _class_heritage(
    heritage: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """``class Foo extends Bar implements IFoo, IBar``."""
    clauses = [child for child in heritage.children if child.type in _CLAUSE_KINDS]
    for clause in clauses:
        kind = _CLAUSE_KINDS[clause.type]
        _type_clause(clause, name, line, src, kind, kind, out)
    if clauses:
        return
    # TypeScript's grammar wraps the base in a clause node; JavaScript's puts
    # the keyword and the base directly under the heritage node.
    for child in heritage.children:
        if child.type not in _UNWRAPPED_BASE_TYPES:
            continue
        parent = bare_type_name(node_text(child, src))
        if parent:
            out.append(
                HeritageRelation(child_name=name, parent_name=parent, kind="extends", line=line)
            )


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
