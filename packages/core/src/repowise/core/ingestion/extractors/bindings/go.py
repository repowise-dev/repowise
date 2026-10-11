"""Go import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_go_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Go import specs."""
    alias_node = stmt_node.child_by_field_name("name")
    path_node = stmt_node.child_by_field_name("path")

    if path_node is None:
        for child in stmt_node.children:
            if child.type == "interpreted_string_literal":
                path_node = child
                break
    if path_node is None:
        return [], []

    path_text = node_text(path_node, src).strip("\"'` ")
    default_name = path_text.rsplit("/", 1)[-1]

    if alias_node:
        alias = node_text(alias_node, src)
        if alias == ".":
            return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
        if alias == "_":
            return [], []
        return [alias], [
            NamedBinding(
                local_name=alias, exported_name=None, source_file=None, is_module_alias=True
            )
        ]

    return [default_name], [
        NamedBinding(
            local_name=default_name,
            exported_name=None,
            source_file=None,
            is_module_alias=True,
        )
    ]
