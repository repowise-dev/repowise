"""Kotlin heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_kotlin_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Kotlin: class Foo : Bar(), IFoo."""
    for child in def_node.children:
        if child.type == "delegation_specifier":
            for delegate in child.children:
                text = node_text(delegate, src).strip()
                bare = text.split("(")[0].split(".")[-1].strip()
                if bare and bare != name:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bare,
                            kind="extends",
                            line=line,
                        )
                    )
        elif child.type == "delegation_specifiers":
            for delegate in child.children:
                if delegate.type in (":", ","):
                    continue
                text = node_text(delegate, src).strip()
                bare = text.split("(")[0].split(".")[-1].strip()
                if bare and bare != name:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bare,
                            kind="extends",
                            line=line,
                        )
                    )
