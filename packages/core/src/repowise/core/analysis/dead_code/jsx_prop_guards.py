"""AST helper for extracting guarded JSX component renders."""

from __future__ import annotations

from typing import Any

from tree_sitter import Language, Parser


def extract_guarded_jsx_renders(
    file_path: str,
    src: str,
) -> list[tuple[str, str, str]]:
    """Extract (parent_fn_name, child_component_name, guard_prop_name) triples.

    Parses JS/TSX files and finds JSX component elements rendered behind binary (&&)
    or ternary prop guards.
    """
    if not file_path.endswith((".tsx", ".jsx", ".ts", ".js")):
        return []

    try:
        if file_path.endswith((".tsx", ".ts")):
            import tree_sitter_typescript as tstype

            lang = Language(tstype.language_tsx())
        else:
            import tree_sitter_javascript as tsjs

            lang = Language(tsjs.language())

        parser = Parser(lang)
        tree = parser.parse(src.encode("utf-8"))
    except Exception:
        return []

    code = src.encode("utf-8")
    results: list[tuple[str, str, str]] = []

    def _node_text(n: Any) -> str:
        return code[n.start_byte : n.end_byte].decode("utf-8", errors="replace")

    def _extract_prop_name(n: Any) -> str | None:
        if n.type == "member_expression":
            obj = None
            prop = None
            for c in n.children:
                if c.type in ("identifier", "this"):
                    obj = _node_text(c)
                elif c.type == "property_identifier":
                    prop = _node_text(c)
            if obj in ("props", "this.props") and prop:
                return prop
        elif n.type == "identifier":
            name = _node_text(n)
            if name not in ("true", "false", "undefined", "null"):
                return name
        elif n.type == "unary_expression":
            for c in n.children:
                if c.type != "!":
                    return _extract_prop_name(c)
        return None

    def _find_jsx_targets(n: Any) -> list[str]:
        targets: list[str] = []
        if n.type in ("jsx_self_closing_element", "jsx_opening_element", "jsx_element"):
            if n.type == "jsx_element":
                for c in n.children:
                    if c.type == "jsx_opening_element":
                        targets.extend(_find_jsx_targets(c))
            else:
                for c in n.children:
                    if c.type in ("identifier", "property_identifier"):
                        name = _node_text(c)
                        if name and name[0].isupper():
                            targets.append(name)
                            break
        else:
            for c in n.children:
                targets.extend(_find_jsx_targets(c))
        return targets

    def _walk(n: Any, current_fn: str | None = None) -> None:
        if n.type in ("function_declaration", "arrow_function", "function_expression"):
            fn_name = current_fn
            if n.type == "function_declaration":
                for c in n.children:
                    if c.type == "identifier":
                        fn_name = _node_text(c)
                        break
            elif n.parent and n.parent.type == "variable_declarator":
                for c in n.parent.children:
                    if c.type == "identifier":
                        fn_name = _node_text(c)
                        break
            current_fn = fn_name

        if n.type == "binary_expression":
            op = None
            left = None
            right = None
            for c in n.children:
                if c.type == "&&":
                    op = "&&"
                elif left is None:
                    left = c
                else:
                    right = c
            if op == "&&" and left and right:
                prop_name = _extract_prop_name(left)
                jsx_targets = _find_jsx_targets(right)
                if prop_name and jsx_targets and current_fn:
                    for target in jsx_targets:
                        results.append((current_fn, target, prop_name))

        elif n.type == "ternary_expression":
            cond = None
            consequence = None
            alternative = None
            for c in n.children:
                if c.type not in ("?", ":"):
                    if cond is None:
                        cond = c
                    elif consequence is None:
                        consequence = c
                    else:
                        alternative = c
            if cond and current_fn:
                prop_name = _extract_prop_name(cond)
                if prop_name and consequence:
                    for target in _find_jsx_targets(consequence):
                        results.append((current_fn, target, prop_name))

        for child in n.children:
            _walk(child, current_fn)

    _walk(tree.root_node)
    return results
