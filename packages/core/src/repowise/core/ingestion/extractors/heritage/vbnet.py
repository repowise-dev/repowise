"""VB.NET heritage extraction.

VB.NET separates the two clause kinds syntactically: ``Inherits`` names the
single base class (classes only), ``Implements`` names one or more
interfaces (classes and structures). Interfaces themselves may ``Inherits``
multiple interfaces. The grammar keeps them as distinct clause nodes with
one ``type`` child per name, so classification needs no conventions-based
guessing (contrast C#'s single ``base_list``).
"""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ...type_names import bare_type_name
from ..helpers import node_text


def _extract_vbnet_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """VB.NET: ``Inherits BaseClass`` → extends; ``Implements IFoo, IBar`` → implements."""
    for child in def_node.children:
        if child.type == "inherits_clause":
            kind = "extends"
        elif child.type == "implements_clause":
            kind = "implements"
        else:
            continue
        for type_node in child.children:
            if type_node.type != "type" and not type_node.is_named:
                continue
            parent = bare_type_name(node_text(type_node, src).strip())
            if not parent or parent == name:
                continue
            out.append(
                HeritageRelation(
                    child_name=name,
                    parent_name=parent,
                    kind=kind,  # type: ignore[arg-type]
                    line=line,
                )
            )
