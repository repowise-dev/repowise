"""PHP heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_php_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """PHP: ``class Foo extends Bar implements IFoo, IBar; use TraitName;``."""
    for child in def_node.children:
        if child.type == "base_clause":
            for sub in child.children:
                if sub.type == "name":
                    parent = node_text(sub, src).strip()
                    if parent and parent != name:
                        out.append(
                            HeritageRelation(
                                child_name=name,
                                parent_name=parent,
                                kind="extends",
                                line=line,
                            )
                        )
        elif child.type == "class_interface_clause":
            for sub in child.children:
                if sub.type == "name":
                    parent = node_text(sub, src).strip()
                    if parent and parent != name:
                        out.append(
                            HeritageRelation(
                                child_name=name,
                                parent_name=parent,
                                kind="implements",
                                line=line,
                            )
                        )
        elif child.type == "declaration_list":
            # use TraitName; inside class body
            for stmt in child.children:
                if stmt.type == "use_declaration":
                    for sub in stmt.children:
                        if sub.type == "name":
                            trait_name = node_text(sub, src).strip()
                            if trait_name and trait_name != name:
                                out.append(
                                    HeritageRelation(
                                        child_name=name,
                                        parent_name=trait_name,
                                        kind="mixin",
                                        line=stmt.start_point[0] + 1,
                                    )
                                )
