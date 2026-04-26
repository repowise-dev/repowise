"""Rust import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_rust_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Rust use declarations."""
    arg_node = stmt_node.child_by_field_name("argument")
    if arg_node is None:
        for child in stmt_node.children:
            if child.type not in ("use", ";", "pub", "visibility_modifier"):
                arg_node = child
                break
    if arg_node is None:
        return [], []

    names: list[str] = []
    bindings: list[NamedBinding] = []
    _parse_rust_use_tree(arg_node, src, names, bindings, depth=0)
    return names, bindings


def _parse_rust_use_tree(
    node: Node,
    src: str,
    names: list[str],
    bindings: list[NamedBinding],
    depth: int,
) -> None:
    """Recursively parse a Rust use-tree into named bindings."""
    if depth > 10:
        return

    if node.type == "use_as_clause":
        path_child = node.child_by_field_name("path") or (
            node.children[0] if node.children else None
        )
        alias_child = node.child_by_field_name("alias") or (
            node.children[-1] if len(node.children) >= 2 else None
        )
        if path_child and alias_child and path_child != alias_child:
            exported = node_text(path_child, src).rsplit("::", 1)[-1]
            local = node_text(alias_child, src)
            names.append(local)
            bindings.append(
                NamedBinding(local_name=local, exported_name=exported, source_file=None)
            )
        return

    if node.type == "use_wildcard":
        names.append("*")
        bindings.append(NamedBinding(local_name="*", exported_name=None, source_file=None))
        return

    if node.type == "use_list":
        for child in node.children:
            if child.type in ("{", "}", ","):
                continue
            _parse_rust_use_tree(child, src, names, bindings, depth + 1)
        return

    if node.type == "scoped_use_list":
        for child in node.children:
            if child.type == "use_list":
                _parse_rust_use_tree(child, src, names, bindings, depth + 1)
        return

    text = node_text(node, src)
    bare = text.rsplit("::", 1)[-1]
    if bare and bare != "*":
        names.append(bare)
        bindings.append(NamedBinding(local_name=bare, exported_name=bare, source_file=None))
