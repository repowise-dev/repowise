"""Scala heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ._types import type_runs

# A superclass constructor call sits inside the extends clause but is not part
# of the parent's name.
_SKIP = frozenset({"arguments"})
_SEPARATORS = frozenset({"extends", "with"})


def _extract_scala_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Scala: ``class Foo extends Bar with Trait1 with Trait2``."""
    for child in def_node.children:
        if child.type != "extends_clause":
            continue
        for separator, parent in type_runs(child, src, _SEPARATORS, _SKIP):
            if parent == name:
                continue
            out.append(
                HeritageRelation(
                    child_name=name,
                    parent_name=parent,
                    kind="implements" if separator == "with" else "extends",
                    line=line,
                )
            )
