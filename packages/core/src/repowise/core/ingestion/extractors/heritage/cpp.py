"""C++ heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_cpp_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """C++: class Foo : public Bar, protected Baz."""
    for child in def_node.children:
        if child.type == "base_class_clause":
            for base in child.children:
                if base.type in (":", ","):
                    continue
                text = node_text(base, src).strip()
                for prefix in ("public", "protected", "private", "virtual"):
                    text = text.removeprefix(prefix).strip()
                bare = text.split("::")[-1].strip()
                if bare:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bare,
                            kind="extends",
                            line=line,
                        )
                    )
