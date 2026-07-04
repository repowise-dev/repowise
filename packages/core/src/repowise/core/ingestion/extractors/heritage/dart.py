"""Dart heritage extraction.

A single ``class_definition`` header can carry all three clause kinds:
``class Foo extends Bar with M1, M2 implements IFoo`` — the grammar nests
the ``with`` mixins inside the ``superclass`` node. ``mixin M on Base``
constrains the mixin to subtypes of ``Base``; that is recorded as
``extends`` (the closest semantic: members of ``Base`` are in scope).
"""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _append(out: list[HeritageRelation], name: str, parent: str, kind: str, line: int) -> None:
    if parent and parent != name:
        out.append(
            HeritageRelation(
                child_name=name,
                parent_name=parent,
                kind=kind,  # type: ignore[arg-type]
                line=line,
            )
        )


def _extract_dart_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    if def_node.type == "class_definition":
        for child in def_node.children:
            if child.type == "superclass":
                for sub in child.children:
                    if sub.type == "type_identifier":
                        _append(out, name, node_text(sub, src).strip(), "extends", line)
                    elif sub.type == "mixins":
                        for mixin in sub.children:
                            if mixin.type == "type_identifier":
                                _append(out, name, node_text(mixin, src).strip(), "mixin", line)
            elif child.type == "interfaces":
                for sub in child.children:
                    if sub.type == "type_identifier":
                        _append(out, name, node_text(sub, src).strip(), "implements", line)
    elif def_node.type == "mixin_declaration":
        # ``mixin M on Base`` — the ``on`` constraint types are direct
        # type_identifier children of the declaration.
        for child in def_node.children:
            if child.type == "type_identifier":
                _append(out, name, node_text(child, src).strip(), "extends", line)
