"""Swift heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ...type_names import bare_type_name
from ..helpers import node_text


def _user_type_identifiers(spec: Node, src: str) -> list[str]:
    """Yield the bare name of each ``user_type`` in *spec*.

    A qualified conformance is several ``type_identifier`` children of one
    ``user_type``, so the node's whole text is normalised once — picking the
    children individually names the qualifier as a parent in its own right.
    """
    names: list[str] = []
    for type_child in spec.children:
        if type_child.type != "user_type":
            continue
        parent = bare_type_name(node_text(type_child, src))
        if parent:
            names.append(parent)
    return names


def _extract_swift_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Swift: ``class Foo: Bar, Protocol1`` — inheritance via ``:`` separator."""
    for child in def_node.children:
        if child.type != "inheritance_specifier":
            continue
        for parent in _user_type_identifiers(child, src):
            if parent != name:
                out.append(
                    HeritageRelation(
                        child_name=name,
                        parent_name=parent,
                        kind="extends",
                        line=line,
                    )
                )
