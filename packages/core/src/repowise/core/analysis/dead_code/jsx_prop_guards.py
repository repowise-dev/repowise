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

    def _extract_prop_names(n: Any, local_vars: set[str] | None = None) -> list[str]:
        if n.type == "parenthesized_expression":
            for c in n.children:
                if c.type not in ("(", ")"):
                    return _extract_prop_names(c, local_vars)
        elif n.type == "member_expression":
            obj_node = n.children[0]
            obj_text = _node_text(obj_node)
            prop_text = None
            for c in n.children:
                if c.type == "property_identifier":
                    prop_text = _node_text(c)
            if (
                obj_text in ("props", "this.props", "self.props")
                or obj_text.endswith(".props")
            ) and prop_text:
                return [prop_text]
        elif n.type == "identifier":
            name = _node_text(n)
            if name not in ("true", "false", "undefined", "null") and (
                local_vars is None or name not in local_vars
            ):
                return [name]
        elif n.type == "unary_expression":
            op = None
            operand = None
            for c in n.children:
                if c.type == "!":
                    op = "!"
                else:
                    operand = c
            if op == "!":
                if operand and operand.type == "unary_expression":
                    sub_op = None
                    sub_operand = None
                    for sub in operand.children:
                        if sub.type == "!":
                            sub_op = "!"
                        else:
                            sub_operand = sub
                    if sub_op == "!" and sub_operand:
                        return _extract_prop_names(sub_operand, local_vars)
                return []
        elif n.type == "binary_expression":
            op = None
            left = None
            right = None
            for c in n.children:
                if c.type in ("&&", "===", "==", "!==", "!="):
                    op = c.type
                elif left is None:
                    left = c
                else:
                    right = c
            if op in ("&&", "===", "==") and left and right:
                return _extract_prop_names(left, local_vars) + _extract_prop_names(right, local_vars)
            elif op in ("!==", "!=") and left and right:
                right_text = _node_text(right)
                left_text = _node_text(left)
                if right_text in ("undefined", "null", "false"):
                    return _extract_prop_names(left, local_vars)
                elif left_text in ("undefined", "null", "false"):
                    return _extract_prop_names(right, local_vars)
        return []

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
                    elif c.type == "member_expression":
                        prop_name = None
                        for sub in c.children:
                            if sub.type == "property_identifier":
                                prop_name = _node_text(sub)
                        if prop_name and prop_name[0].isupper():
                            targets.append(prop_name)
                            break
        else:
            for c in n.children:
                targets.extend(_find_jsx_targets(c))
        return targets

    def _extract_bound_names(n: Any) -> list[str]:
        names: list[str] = []
        if n.type in ("identifier", "shorthand_property_identifier_pattern"):
            names.append(_node_text(n))
        elif n.type in (
            "array_pattern",
            "object_pattern",
            "pair_pattern",
            "variable_declarator",
            "rest_pattern",
        ):
            for c in n.children:
                if c.type not in (":", "=", ",", "[", "]", "{", "}", "...", "var", "let", "const"):
                    names.extend(_extract_bound_names(c))
        return names

    def _collect_function_local_vars(fn_node: Any) -> set[str]:
        local_vars: set[str] = set()

        def _scan(n: Any) -> None:
            if n != fn_node and n.type in (
                "function_declaration",
                "arrow_function",
                "function_expression",
                "method_definition",
            ):
                return
            if n.type == "variable_declarator":
                for c in n.children:
                    if c.type in ("identifier", "array_pattern", "object_pattern"):
                        local_vars.update(_extract_bound_names(c))
                        break
            for c in n.children:
                _scan(c)

        _scan(fn_node)
        return local_vars

    def _walk(n: Any, current_fn: str | None = None, local_vars: set[str] | None = None) -> None:
        current_local_vars = local_vars
        if n.type in (
            "function_declaration",
            "arrow_function",
            "function_expression",
            "method_definition",
        ):
            fn_name = current_fn
            if n.type in ("function_declaration", "method_definition"):
                for c in n.children:
                    if c.type in ("identifier", "property_identifier"):
                        fn_name = _node_text(c)
                        break
            elif n.parent and n.parent.type == "variable_declarator":
                for c in n.parent.children:
                    if c.type == "identifier":
                        fn_name = _node_text(c)
                        break
            current_fn = fn_name or "Anonymous"
            current_local_vars = _collect_function_local_vars(n)

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
                jsx_targets = _find_jsx_targets(right)
                if jsx_targets and current_fn:
                    prop_names = _extract_prop_names(left, current_local_vars)
                    for prop_name in prop_names:
                        for target in jsx_targets:
                            results.append((current_fn, target, prop_name))

        elif n.type == "ternary_expression":
            cond = None
            consequence = None
            for c in n.children:
                if c.type not in ("?", ":"):
                    if cond is None:
                        cond = c
                    elif consequence is None:
                        consequence = c
            if cond and consequence and current_fn:
                jsx_targets = _find_jsx_targets(consequence)
                if jsx_targets:
                    prop_names = _extract_prop_names(cond, current_local_vars)
                    for prop_name in prop_names:
                        for target in jsx_targets:
                            results.append((current_fn, target, prop_name))

        for child in n.children:
            _walk(child, current_fn, current_local_vars)

    _walk(tree.root_node)
    return results
