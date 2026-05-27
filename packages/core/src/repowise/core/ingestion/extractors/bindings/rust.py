"""Rust import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_rust_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Rust use declarations and mod items."""
    # `mod foo;` (without body) declares a child module — treat as wildcard
    # import because all public symbols become accessible via `foo::Name`.
    if stmt_node.type == "mod_item":
        return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]

    # `extern crate foo;` or `extern crate foo as bar;`
    if stmt_node.type == "extern_crate_declaration":
        alias_node = stmt_node.child_by_field_name("alias")
        name_node = stmt_node.child_by_field_name("name")
        if alias_node:
            local = node_text(alias_node, src)
            exported = node_text(name_node, src) if name_node else local
            return [local], [NamedBinding(
                local_name=local, exported_name=exported,
                source_file=None, is_module_alias=True,
            )]
        elif name_node:
            name = node_text(name_node, src)
            return [name], [NamedBinding(
                local_name=name, exported_name=name,
                source_file=None, is_module_alias=True,
            )]
        return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]

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
