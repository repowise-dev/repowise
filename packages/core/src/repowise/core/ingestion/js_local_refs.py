"""Extract same-file TypeScript/JavaScript symbol references.

The dependency graph records calls and imports, but value references such as
``process.on('SIGTERM', shutdown)`` do not need either edge. Keep a compact
rescue set on each file node for names referenced outside their own symbol
definition.
"""

from __future__ import annotations

from tree_sitter import Language, Parser

__all__ = ["extract_js_local_refs"]


def extract_js_local_refs(
    file_path: str,
    source: str,
    defined_names: set[str] | frozenset[str],
    root_node=None,
) -> frozenset[str]:
    """Return defined names referenced outside their own definition span."""
    if not defined_names:
        return frozenset()
    try:
        if root_node is not None:
            root = root_node
        elif file_path.endswith((".tsx", ".ts")):
            import tree_sitter_typescript as ts

            language = Language(
                ts.language_tsx() if file_path.endswith(".tsx") else ts.language_typescript()
            )
            root = Parser(language).parse(source.encode("utf-8")).root_node
        else:
            import tree_sitter_javascript as js

            language = Language(js.language())
            root = Parser(language).parse(source.encode("utf-8")).root_node
    except Exception:
        return frozenset()

    code = source.encode("utf-8")
    declarations_by_name: dict[str, list[tuple[int, int]]] = {}
    declaration_types = {
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "variable_declarator",
        "enum_declaration",
        "method_definition",
    }
    scopes: list[tuple[int, int, set[str]]] = []
    scan = [root]
    while scan:
        item = scan.pop()
        name_node = item.child_by_field_name("name")
        if (
            item.type in declaration_types
            and name_node is not None
            and name_node.type in ("identifier", "type_identifier")
        ):
            name = code[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
            if name in defined_names:
                declarations_by_name.setdefault(name, []).append((item.start_byte, item.end_byte))
        if item.type in (
            "function_declaration",
            "function_expression",
            "arrow_function",
            "method_definition",
        ):
            local_names: set[str] = set()
            parameters = item.child_by_field_name("parameters")
            if parameters is not None:
                _collect_pattern_names(parameters, code, local_names)
            body = item.child_by_field_name("body")
            nested = [body] if body is not None else []
            while nested:
                child = nested.pop()
                if child != body and child.type in (
                    "function_declaration",
                    "function_expression",
                    "arrow_function",
                    "method_definition",
                ):
                    continue
                if child.type == "variable_declarator":
                    pattern = child.child_by_field_name("name")
                    if pattern is not None:
                        _collect_pattern_names(pattern, code, local_names)
                nested.extend(child.children)
            scopes.append((item.start_byte, item.end_byte, local_names))
        scan.extend(item.children)

    shadow_spans: dict[str, list[tuple[int, int]]] = {}
    for start, end, local_names in scopes:
        for name in local_names & defined_names:
            shadow_spans.setdefault(name, []).append((start, end))

    found: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in ("identifier", "type_identifier", "shorthand_property_identifier"):
            name = code[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            if name in defined_names:
                # A self-recursive use and a function-local shadow do not
                # prove that the top-level declaration is reachable.
                in_definition = any(
                    start <= node.start_byte < end
                    for start, end in declarations_by_name.get(name, ())
                )
                shadowed = any(
                    start <= node.start_byte < end for start, end in shadow_spans.get(name, ())
                )
                if not in_definition and not shadowed:
                    found.add(name)
        stack.extend(reversed(node.children))
    return frozenset(found)


def _collect_pattern_names(node, code: bytes, names: set[str]) -> None:
    if node.type in ("identifier", "shorthand_property_identifier_pattern"):
        names.add(code[node.start_byte : node.end_byte].decode("utf-8", errors="replace"))
        return
    if node.type in ("type_annotation", "type_parameters", "type_arguments"):
        return
    for child in node.children:
        _collect_pattern_names(child, code, names)
