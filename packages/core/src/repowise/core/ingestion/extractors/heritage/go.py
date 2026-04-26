"""Go heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ..helpers import node_text


def _extract_go_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Go: struct embedding (type Foo struct { Bar; baz.Qux })."""
    type_node = def_node.child_by_field_name("type")
    if type_node is None:
        return

    if type_node.type == "struct_type":
        body = type_node.child_by_field_name("body") or type_node
        if body is None:
            return
        for field_decl in body.children:
            if field_decl.type != "field_declaration":
                continue
            name_node = field_decl.child_by_field_name("name")
            type_child = field_decl.child_by_field_name("type")
            if name_node is None and type_child is not None:
                parent = node_text(type_child, src).strip().lstrip("*")
                bare = parent.split(".")[-1]
                if bare:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bare,
                            kind="mixin",
                            line=line,
                        )
                    )

    elif type_node.type == "interface_type":
        for child in type_node.children:
            if child.type in ("{", "}", "\n"):
                continue
            if child.type in ("type_identifier", "qualified_type"):
                parent = node_text(child, src).strip()
                bare = parent.split(".")[-1]
                if bare:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=bare,
                            kind="extends",
                            line=line,
                        )
                    )
