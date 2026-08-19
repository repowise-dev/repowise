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

    def _extract_prop_names(
        n: Any,
        local_vars: set[str] | None = None,
        param_props: set[str] | None = None,
    ) -> list[str]:
        if n.type == "parenthesized_expression":
            for c in n.children:
                if c.type not in ("(", ")"):
                    return _extract_prop_names(c, local_vars, param_props)
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
            if name in ("true", "false", "undefined", "null"):
                return []
            if local_vars is not None and name in local_vars:
                return []
            if param_props is not None:
                return [name] if name in param_props else []
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
                        return _extract_prop_names(sub_operand, local_vars, param_props)
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
                return _extract_prop_names(left, local_vars, param_props) + _extract_prop_names(
                    right, local_vars, param_props
                )
            elif op in ("!==", "!=") and left and right:
                right_text = _node_text(right)
                left_text = _node_text(left)
                if right_text in ("undefined", "null", "false"):
                    return _extract_prop_names(left, local_vars, param_props)
                elif left_text in ("undefined", "null", "false"):
                    return _extract_prop_names(right, local_vars, param_props)
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

    def _collect_module_local_vars(root: Any) -> set[str]:
        mod_vars: set[str] = set()
        for child in root.children:
            target_node = child
            if child.type == "export_statement":
                for c in child.children:
                    if c.type in ("lexical_declaration", "variable_declaration"):
                        target_node = c
                        break
            if target_node.type in ("lexical_declaration", "variable_declaration"):
                for dec in target_node.children:
                    if dec.type == "variable_declarator":
                        for c in dec.children:
                            if c.type in ("identifier", "array_pattern", "object_pattern"):
                                mod_vars.update(_extract_bound_names(c))
                                break
        return mod_vars

    module_vars = _collect_module_local_vars(tree.root_node)

    def _collect_function_param_props(fn_node: Any) -> set[str] | None:
        params_node = None
        for c in fn_node.children:
            if c.type in ("formal_parameters", "parameters"):
                params_node = c
                break
        if not params_node:
            return None

        param_props: set[str] = set()
        has_destructured_param = False

        for param in params_node.children:
            if param.type in ("(", ")", ",", ":"):
                continue
            p = param
            if p.type == "required_parameter":
                for child in p.children:
                    if child.type in ("object_pattern", "identifier", "assignment_pattern"):
                        p = child
                        break
            if p.type == "assignment_pattern":
                for child in p.children:
                    if child.type in ("object_pattern", "identifier"):
                        p = child
                        break

            if p.type == "object_pattern":
                has_destructured_param = True
                param_props.update(_extract_bound_names(p))

        return param_props if has_destructured_param else None

    def _collect_function_local_vars(fn_node: Any) -> set[str]:
        local_vars: set[str] = set(module_vars)

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

    def _walk(
        n: Any,
        current_fn: str | None = None,
        local_vars: set[str] | None = None,
        param_props: set[str] | None = None,
    ) -> None:
        current_local_vars = local_vars
        current_param_props = param_props
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
            current_param_props = _collect_function_param_props(n)

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
                    prop_names = _extract_prop_names(left, current_local_vars, current_param_props)
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
                    prop_names = _extract_prop_names(cond, current_local_vars, current_param_props)
                    for prop_name in prop_names:
                        for target in jsx_targets:
                            results.append((current_fn, target, prop_name))

        for child in n.children:
            _walk(child, current_fn, current_local_vars, current_param_props)

    _walk(tree.root_node)
    return results
