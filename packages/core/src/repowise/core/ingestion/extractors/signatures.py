"""Signature building for parsed symbols."""

from __future__ import annotations

from tree_sitter import Node

from .helpers import node_text


def build_signature(node_type: str, name: str, params_text: str, def_node: Node, src: str) -> str:
    """Build a human-readable signature string."""

    # Helper: try multiple field names for "return type", fall back gracefully.
    def _ret(fields: tuple[str, ...]) -> str:
        for f in fields:
            n = def_node.child_by_field_name(f)
            if n is not None:
                return f" -> {node_text(n, src)}"
        return ""

    if node_type == "function_definition":
        # Detect async via child "async" keyword (tree-sitter-python >= 0.23)
        prefix = "async " if any(c.type == "async" for c in def_node.children) else ""
        return f"{prefix}def {name}{params_text}{_ret(('return_type',))}"
    if node_type == "function_item":
        # Rust: return_type field
        return f"fn {name}{params_text}{_ret(('return_type',))}"
    if node_type in ("function_declaration", "generator_function_declaration"):
        # TS/JS use return_type; Go uses result
        return f"function {name}{params_text}{_ret(('return_type', 'result'))}"
    if node_type in ("class_definition", "class_declaration", "abstract_class_declaration"):
        base = f"class {name}"
        if params_text:
            base += params_text
        return base
    if node_type == "interface_declaration":
        return f"interface {name}"
    if node_type == "type_alias_declaration":
        return f"type {name}"
    if node_type == "enum_declaration":
        return f"enum {name}"
    if node_type == "method_definition":
        # TypeScript/JavaScript class method
        return f"{name}{params_text}{_ret(('return_type',))}"
    if node_type == "method_declaration":
        # Go method: include receiver text and result type
        recv_node = def_node.child_by_field_name("receiver")
        recv_text = node_text(recv_node, src) if recv_node else ""
        recv_prefix = f"{recv_text} " if recv_text else ""
        return f"func {recv_prefix}{name}{params_text}{_ret(('result',))}"
    if node_type in ("struct_item", "struct_specifier"):
        return f"struct {name}"
    if node_type in ("enum_item", "enum_specifier"):
        return f"enum {name}"
    if node_type == "trait_item":
        return f"trait {name}"
    if node_type == "impl_item":
        return f"impl {name}"
    if node_type in ("class_specifier",):
        return f"class {name}"
    # Fallback
    return f"{name}{params_text}"
