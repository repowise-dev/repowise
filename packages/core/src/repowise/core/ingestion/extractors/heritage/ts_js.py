"""TypeScript / JavaScript heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_ts_js_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """TypeScript/JavaScript: class Foo extends Bar implements IFoo, IBar."""
    for child in def_node.children:
        if child.type == "class_heritage":
            for clause in child.children:
                if clause.type == "extends_clause":
                    for type_node in clause.children:
                        if type_node.type in ("extends", ","):
                            continue
                        parent = node_text(type_node, src).strip()
                        if parent:
                            out.append(
                                HeritageRelation(
                                    child_name=name,
                                    parent_name=parent,
                                    kind="extends",
                                    line=line,
                                )
                            )
                elif clause.type == "implements_clause":
                    for type_node in clause.children:
                        if type_node.type in ("implements", ","):
                            continue
                        parent = node_text(type_node, src).strip()
                        if parent:
                            out.append(
                                HeritageRelation(
                                    child_name=name,
                                    parent_name=parent,
                                    kind="implements",
                                    line=line,
                                )
                            )
        # interface extends: interface Foo extends Bar
        if child.type == "extends_type_clause":
            for type_node in child.children:
                if type_node.type in ("extends", ","):
                    continue
                parent = node_text(type_node, src).strip()
                if parent:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=parent,
                            kind="extends",
                            line=line,
                        )
                    )
