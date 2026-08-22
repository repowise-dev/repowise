"""Go heritage extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import HeritageRelation
from ...type_names import strip_type_arguments
from ..helpers import node_text


def _extract_go_heritage(
    def_node: Node, name: str, line: int, src: str, out: list[HeritageRelation]
) -> None:
    """Go: struct embedding (type Foo struct { Bar; baz.Qux })."""
    type_node = def_node.child_by_field_name("type")
    if type_node is None:
        return

    if type_node.type == "struct_type":
        body = type_node.child_by_field_name("body")
        if body is None:
            for child in type_node.children:
                if child.type == "field_declaration_list":
                    body = child
                    break
        if body is None:
            return
        for field_decl in body.children:
            if field_decl.type != "field_declaration":
                continue
            name_node = field_decl.child_by_field_name("name")
            type_child = field_decl.child_by_field_name("type")
            if name_node is None and type_child is not None:
                parent = node_text(type_child, src).strip().lstrip("*")
                # Keep the package qualifier: ``io.Reader`` must stay
                # ``io.Reader``, not ``Reader``, or an embed of a stdlib type
                # binds to whatever repo-local type shares the short name (and
                # can inherit from itself). Type arguments are still stripped.
                parent = strip_type_arguments(parent)
                if parent:
                    out.append(
                        HeritageRelation(
                            child_name=name,
                            parent_name=parent,
                            kind="mixin",
                            line=line,
                        )
                    )

    elif type_node.type == "interface_type":
        for child in type_node.children:
            if child.type != "type_elem":
                continue
            text = node_text(child, src).strip()
            # A type set (`~int | string`) wears the same node as an embed but
            # is a generic bound, carrying no methods to inherit.
            if "|" in text or "~" in text:
                continue
            # Keep the package qualifier (see the struct branch above).
            parent = strip_type_arguments(text)
            if parent:
                out.append(
                    HeritageRelation(
                        child_name=name,
                        parent_name=parent,
                        kind="extends",
                        line=line,
                    )
                )
