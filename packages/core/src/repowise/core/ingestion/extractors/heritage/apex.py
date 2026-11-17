"""Apex heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ...type_names import bare_type_name
from ..helpers import node_text
from .java import _interfaces


def _extract_apex_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """``class Foo extends Bar implements IFoo`` / ``interface IFoo extends IBar``.

    Java's shape, except that the superclass is read from its type node: Apex
    keywords are case-insensitive, so the clause's text may open ``EXTENDS``.
    """
    superclass = def_node.child_by_field_name("superclass")
    if superclass is not None and superclass.named_children:
        parent = bare_type_name(node_text(superclass.named_children[-1], src))
        if parent:
            out.append(HeritageRelation(child_name=name, parent_name=parent, kind="extends", line=line))
    _interfaces(def_node, name, line, src, out)
