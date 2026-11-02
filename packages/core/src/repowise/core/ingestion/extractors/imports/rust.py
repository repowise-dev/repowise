"""Rust ``use`` declarations, ``mod`` items and module-declaring macros."""

from __future__ import annotations

import re

from tree_sitter import Node

from ...models import Import, NamedBinding
from ..bindings.rust import expand_rust_use_tree, macro_mod_imports, rust_use_argument
from ..helpers import node_text
from ._types import ImportExtractor
from .generic import _generic_import


def _rust_use_imports(stmt_node: Node, module_text: str, raw: str, src: str) -> list[Import]:
    """Rust: one Import per leaf of the use tree, each with its own path.

    ``use crate::{a::B, c::D}`` names two modules, and the joined string
    resolves to neither. ``super`` hops that only climb out of inline
    ``mod`` blocks stay inside this file, so they are dropped here and the
    resolver sees the path the file itself would write.
    """
    arg_node = rust_use_argument(stmt_node)
    leaves = expand_rust_use_tree(arg_node, src) if arg_node is not None else []
    inline_depth = _rust_inline_mod_depth(stmt_node)
    # ``pub use`` re-exports the module.
    is_reexport = any(child.type == "visibility_modifier" for child in stmt_node.children)
    if len(leaves) <= 1 and "{" not in module_text and not inline_depth:
        return [_generic_import(stmt_node, module_text, raw, "rust", src, is_reexport=is_reexport)]
    imports = []
    for path, local, exported in leaves:
        path = _strip_inline_super(path, inline_depth)
        imports.append(
            Import(
                raw_statement=raw,
                module_path=path,
                imported_names=[local],
                is_relative=path.startswith(("self::", "super::", "crate::")),
                resolved_file=None,
                bindings=[NamedBinding(local_name=local, exported_name=exported, source_file=None)],
                is_reexport=is_reexport,
            )
        )
    return imports


def _rust_inline_mod_depth(node: Node) -> int:
    """How many inline ``mod name { ... }`` blocks enclose *node* in its file."""
    depth = 0
    parent = node.parent
    while parent is not None:
        if parent.type == "mod_item":
            depth += 1
        parent = parent.parent
    return depth


def _strip_inline_super(path: str, inline_depth: int) -> str:
    """Drop the ``super`` hops of *path* that stay inside the file.

    In ``mod tests { use super::*; }`` the ``super`` is this file's own
    module, so the path is ``self::*``, not the parent module's glob.
    """
    if not inline_depth:
        return path
    segments = path.split("::")
    hops = 0
    while hops < len(segments) and segments[hops] == "super":
        hops += 1
    if not hops:
        return path
    if hops <= inline_depth:
        return "::".join(["self", *segments[hops:]])
    return "::".join(segments[inline_depth:])


def _rust_mod_path_attribute(stmt_node: Node, src: str) -> str | None:
    """The ``#[path = "..."]`` override on a Rust ``mod`` item, if any.

    Outer attributes are preceding siblings of the item, not children.
    """
    parent = stmt_node.parent
    if parent is None:
        return None
    siblings = parent.children
    for j, sib in enumerate(siblings):
        if sib.id != stmt_node.id:
            continue
        # Walk backward through preceding attribute_item siblings
        k = j - 1
        while k >= 0 and siblings[k].type == "attribute_item":
            path_match = re.search(r'path\s*=\s*"([^"]+)"', node_text(siblings[k], src))
            if path_match:
                return path_match.group(1)
            k -= 1
        return None
    return None


def _rust_statement(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, language: str, src: str
) -> list[Import]:
    if stmt_node.type == "use_declaration":
        return _rust_use_imports(stmt_node, module_text, raw, src)
    if stmt_node.type == "macro_invocation":
        # Most top-level calls (``s! { struct ... }``) declare no module.
        return macro_mod_imports(module_node, raw) if "mod" in raw else []
    if stmt_node.type == "mod_item":
        module_text = _rust_mod_path_attribute(stmt_node, src) or module_text
        return [
            _generic_import(stmt_node, module_text, raw, language, src, is_module_declaration=True)
        ]
    return [_generic_import(stmt_node, module_text, raw, language, src)]


def _rust_dedup_key(stmt_node: Node, raw: str, src: str) -> str:
    # A ``mod`` item's text excludes its outer attributes, so two ``#[cfg]``-gated
    # ``mod imp;`` items with different ``#[path]`` files would share one key.
    if stmt_node.type == "mod_item":
        mod_path_attr = _rust_mod_path_attribute(stmt_node, src)
        if mod_path_attr is not None:
            return f"{raw}|path={mod_path_attr}"
    return raw


RUST_IMPORTS = ImportExtractor(statement=_rust_statement, dedup_key=_rust_dedup_key)
