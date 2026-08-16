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
from ._types import type_runs

_SUPERCLASS_SEPARATORS = frozenset({"extends"})
_MIXIN_SEPARATORS = frozenset({"with"})
_INTERFACE_SEPARATORS = frozenset({"implements"})
# The mixin list is a child of ``superclass`` and is read separately.
_SUPERCLASS_SKIP = frozenset({"mixins"})
_ON_SEPARATORS = frozenset({"on"})
_MIXIN_DECL_SKIP = frozenset({"class_body"})


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
                for _, parent in type_runs(
                    child, src, _SUPERCLASS_SEPARATORS, _SUPERCLASS_SKIP
                ):
                    _append(out, name, parent, "extends", line)
                for sub in child.children:
                    if sub.type == "mixins":
                        for _, mixin in type_runs(sub, src, _MIXIN_SEPARATORS):
                            _append(out, name, mixin, "mixin", line)
            elif child.type == "interfaces":
                for _, parent in type_runs(child, src, _INTERFACE_SEPARATORS):
                    _append(out, name, parent, "implements", line)
    elif def_node.type == "mixin_declaration":
        # ``mixin M on Base`` — the constraints are flat siblings of the
        # declaration, sharing it with the mixin's own name and body, so only
        # the runs the ``on`` keyword introduces are constraints.
        for separator, parent in type_runs(
            def_node, src, _ON_SEPARATORS, _MIXIN_DECL_SKIP
        ):
            if separator == "on":
                _append(out, name, parent, "extends", line)
