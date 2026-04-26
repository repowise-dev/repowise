"""Scala heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_scala_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Scala: ``class Foo extends Bar with Trait1 with Trait2``."""
    for child in def_node.children:
        if child.type == "extends_clause":
            saw_with = False
            for sub in child.children:
                if sub.type == "extends":
                    continue
                if sub.type == "with":
                    saw_with = True
                    continue
                if sub.type == "type_identifier":
                    parent = node_text(sub, src).strip()
                    if parent and parent != name:
                        kind = "implements" if saw_with else "extends"
                        out.append(
                            HeritageRelation(
                                child_name=name,
                                parent_name=parent,
                                kind=kind,
                                line=line,
                            )
                        )
