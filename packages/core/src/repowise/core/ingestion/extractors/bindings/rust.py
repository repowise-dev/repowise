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

    arg_node = rust_use_argument(stmt_node)
    if arg_node is None:
        return [], []

    names: list[str] = []
    bindings: list[NamedBinding] = []
    for _path, local, exported in expand_rust_use_tree(arg_node, src):
        names.append(local)
        bindings.append(NamedBinding(local_name=local, exported_name=exported, source_file=None))
    return names, bindings


def rust_use_argument(stmt_node: Node) -> Node | None:
    """The use tree of a ``use_declaration`` (the part after ``use``)."""
    arg_node = stmt_node.child_by_field_name("argument")
    if arg_node is not None:
        return arg_node
    for child in stmt_node.children:
        if child.type not in ("use", ";", "pub", "visibility_modifier"):
            return child
    return None


def expand_rust_use_tree(node: Node, src: str) -> list[tuple[str, str, str | None]]:
    """Flatten a Rust use tree into one ``(path, local_name, exported_name)`` per leaf.

    - ``crate::a::B``                    -> ``[("crate::a::B", "B", "B")]``
    - ``crate::{a::{self, B as C}, d::*}`` -> ``crate::a`` bound as ``a``,
      ``crate::a::B`` bound as ``C``, and ``("crate::d::*", "*", None)``

    Each leaf names one path, so a brace group whose members live in
    different modules resolves member by member instead of as one
    unresolvable ``crate::{...}`` string.
    """
    leaves: list[tuple[str, str, str | None]] = []
    _walk_use_tree(node, src, "", leaves, depth=0)
    return leaves


def _join(prefix: str, path: str) -> str:
    return f"{prefix}::{path}" if prefix else path


def _walk_use_tree(
    node: Node,
    src: str,
    prefix: str,
    leaves: list[tuple[str, str, str | None]],
    depth: int,
) -> None:
    if depth > 10:
        return

    if node.type == "scoped_use_list":
        path_node = node.child_by_field_name("path")
        list_node = node.child_by_field_name("list")
        if path_node is not None:
            prefix = _join(prefix, node_text(path_node, src))
        if list_node is not None:
            _walk_use_tree(list_node, src, prefix, leaves, depth + 1)
        return

    if node.type == "use_list":
        for child in node.named_children:
            _walk_use_tree(child, src, prefix, leaves, depth + 1)
        return

    if node.type == "use_wildcard":
        leaves.append((_join(prefix, node_text(node, src)), "*", None))
        return

    alias: str | None = None
    if node.type == "use_as_clause":
        path_node = node.child_by_field_name("path")
        alias_node = node.child_by_field_name("alias")
        if path_node is None or alias_node is None:
            return
        alias = node_text(alias_node, src)
        node = path_node

    text = node_text(node, src)
    # ``{self}`` names the enclosing path itself: ``a::{self}`` binds ``a``.
    path = prefix if text == "self" and prefix else _join(prefix, text)
    exported = path.rsplit("::", 1)[-1]
    if exported and exported != "*":
        leaves.append((path, alias or exported, exported))
