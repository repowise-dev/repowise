"""F# heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import fsharp_type_name


def _extract_fsharp_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """F#: ``inherit Base(...)`` is extends, ``interface IFoo with`` is implements.

    ``def_node`` is the ``anon_type_defn`` the query captured. F# allows at
    most one ``inherit`` per type and states it explicitly, so unlike Pascal
    there is no first-entry-is-the-base guess: the grammar names both
    relations. A body element is wrapped in its own
    ``type_extension_elements`` node, except ``class_inherits_decl``, which
    is a direct child, so both depths are walked.
    """
    for child in def_node.named_children:
        candidates = (
            [child] if child.type != "type_extension_elements" else list(child.named_children)
        )
        for node in candidates:
            if node.type == "class_inherits_decl":
                kind = "extends"
            elif node.type == "interface_implementation":
                kind = "implements"
            else:
                continue
            base = next(
                (grand for grand in node.named_children if grand.type == "simple_type"),
                None,
            )
            if base is None:
                continue
            # Same walk the type-reference head uses.
            parent = fsharp_type_name(base, src)
            if parent and parent != name:
                out.append(
                    HeritageRelation(
                        child_name=name, parent_name=parent, kind=kind, line=line
                    )
                )
