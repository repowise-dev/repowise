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
                text = node_text(n, src)
                # TypeScript/JavaScript's return_type is a type_annotation
                # node whose text already carries a leading colon (": string"),
                # unlike Python ("int"), Rust ("i32"), or Go ("string"). Strip
                # it so we never emit a doubled " -> : type".
                if text.startswith(":"):
                    text = text[1:].lstrip()
                return f" -> {text}"
        return ""

    if node_type == "function_definition":
        # C/C++ share this node name with Python but expose the declared type
        # through ``type`` rather than ``return_type``.
        if def_node.child_by_field_name("type") is not None:
            return f"{name}{params_text}{_ret(('type',))}"
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
        recv_node = def_node.child_by_field_name("receiver")
        declared_type_fields = ("type", "returns")
        if recv_node is None and any(
            def_node.child_by_field_name(field) is not None for field in declared_type_fields
        ):
            # Java/C#: the declared type is the return type; there is no method keyword.
            return f"{name}{params_text}{_ret(declared_type_fields)}"
        # Go method: include receiver text and result type.
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
    if node_type in ("assignment", "variable_declarator"):
        # Module-level constant/variable: the signature IS the assignment —
        # the verbatim line answers "what is the default value of X" without
        # a follow-up source read. First line only, bounded; multi-line
        # values (dicts, arrays) stay reachable via get_symbol.
        text = node_text(def_node, src)
        first_line = text.splitlines()[0].strip() if text else name
        if len(first_line) > 160:
            first_line = first_line[:157] + "..."
        return first_line
    if node_type == "lexical_declaration":
        # Arrow function assigned to const/let.
        # params_text is either "(a, b)" (formal_parameters) or a bare
        # identifier "x" (unparenthesized single-param arrow: x => ...).
        if params_text and not params_text.startswith("("):
            return f"{name}({params_text})"
        return f"{name}{params_text}"
    # Fallback
    return f"{name}{params_text}"
