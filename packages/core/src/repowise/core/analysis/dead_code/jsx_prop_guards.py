"""Guarded JSX renders, and the walk that asks whether every route to a component is blocked."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tree_sitter import Language, Parser

from ...ingestion.models import REACHABILITY_USE_EDGE_TYPES

_FUNCTION_NODES = ("function_declaration", "arrow_function", "function_expression", "method_definition")
_BINDING_CONTAINERS = (
    "array_pattern",
    "object_pattern",
    "pair_pattern",
    "variable_declarator",
    "rest_pattern",
    "formal_parameters",
    "parameters",
    "required_parameter",
    "optional_parameter",
    "assignment_pattern",
)
_BINDING_PUNCTUATION = (":", "=", ",", "[", "]", "{", "}", "...", "var", "let", "const")
_FALSY_LITERALS = ("undefined", "null", "false")


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

    scan = _GuardScan(src.encode("utf-8"))
    scan.module_vars = scan.module_local_vars(tree.root_node)
    scan.walk(tree.root_node)
    return scan.results


class _GuardScan:
    """One file's guarded-render scan: source bytes, module-level names, results."""

    def __init__(self, code: bytes) -> None:
        self.code = code
        self.module_vars: set[str] = set()
        self.results: list[tuple[str, str, str]] = []

    def text(self, n: Any) -> str:
        return self.code[n.start_byte : n.end_byte].decode("utf-8", errors="replace")

    # -- guard conditions ---------------------------------------------------

    def prop_names(
        self,
        n: Any,
        local_vars: set[str] | None = None,
        param_props: set[str] | None = None,
    ) -> list[str]:
        """Prop names a guard condition *n* tests, or ``[]`` when it tests none."""
        if n.type == "parenthesized_expression":
            for c in n.children:
                if c.type not in ("(", ")"):
                    return self.prop_names(c, local_vars, param_props)
        elif n.type == "member_expression":
            return self._member_prop(n)
        elif n.type == "identifier":
            return self._identifier_prop(n, local_vars, param_props)
        elif n.type == "unary_expression":
            return self._double_negated_props(n, local_vars, param_props)
        elif n.type == "binary_expression":
            return self._comparison_props(n, local_vars, param_props)
        return []

    def _member_prop(self, n: Any) -> list[str]:
        obj_text = self.text(n.children[0])
        prop_text = None
        for c in n.children:
            if c.type == "property_identifier":
                prop_text = self.text(c)
        if (
            obj_text in ("props", "this.props", "self.props") or obj_text.endswith(".props")
        ) and prop_text:
            return [prop_text]
        return []

    def _identifier_prop(
        self, n: Any, local_vars: set[str] | None, param_props: set[str] | None
    ) -> list[str]:
        name = self.text(n)
        if name in ("true", "false", "undefined", "null"):
            return []
        if local_vars is not None and name in local_vars:
            return []
        if param_props is not None:
            return [name] if name in param_props else []
        return [name]

    def _double_negated_props(
        self, n: Any, local_vars: set[str] | None, param_props: set[str] | None
    ) -> list[str]:
        """``!!x`` reads as ``x``; any other unary guard tests no prop."""
        negated, operand = _negation(n)
        if not negated:
            return []
        if operand and operand.type == "unary_expression":
            inner_negated, inner = _negation(operand)
            if inner_negated and inner:
                return self.prop_names(inner, local_vars, param_props)
        return []

    def _comparison_props(
        self, n: Any, local_vars: set[str] | None, param_props: set[str] | None
    ) -> list[str]:
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
        if not (left and right):
            return []
        if op in ("&&", "===", "=="):
            return self.prop_names(left, local_vars, param_props) + self.prop_names(
                right, local_vars, param_props
            )
        if op in ("!==", "!="):
            if self.text(right) in _FALSY_LITERALS:
                return self.prop_names(left, local_vars, param_props)
            if self.text(left) in _FALSY_LITERALS:
                return self.prop_names(right, local_vars, param_props)
        return []

    # -- guarded components -------------------------------------------------

    def jsx_targets(self, n: Any) -> list[str]:
        """Capitalised component names rendered by JSX elements under *n*."""
        if n.type == "jsx_element":
            targets: list[str] = []
            for c in n.children:
                if c.type == "jsx_opening_element":
                    targets.extend(self.jsx_targets(c))
            return targets
        if n.type in ("jsx_self_closing_element", "jsx_opening_element"):
            name = self._element_component(n)
            return [name] if name else []
        targets = []
        for c in n.children:
            targets.extend(self.jsx_targets(c))
        return targets

    def _element_component(self, n: Any) -> str | None:
        """The first capitalised tag name of an opening or self-closing element."""
        for c in n.children:
            if c.type in ("identifier", "property_identifier"):
                name = self.text(c)
            elif c.type == "member_expression":
                name = self._last_property(c)
            else:
                continue
            if name and name[0].isupper():
                return name
        return None

    def _last_property(self, member: Any) -> str | None:
        prop_name = None
        for sub in member.children:
            if sub.type == "property_identifier":
                prop_name = self.text(sub)
        return prop_name

    # -- bindings -----------------------------------------------------------

    def bound_names(self, n: Any) -> list[str]:
        """Every name a declaration or parameter pattern binds."""
        if n.type in ("identifier", "shorthand_property_identifier_pattern"):
            return [self.text(n)]
        names: list[str] = []
        if n.type in _BINDING_CONTAINERS:
            for c in n.children:
                if c.type not in _BINDING_PUNCTUATION:
                    names.extend(self.bound_names(c))
        return names

    def _declarator_names(self, declarator: Any) -> list[str]:
        for c in declarator.children:
            if c.type in ("identifier", "array_pattern", "object_pattern"):
                return self.bound_names(c)
        return []

    def module_local_vars(self, root: Any) -> set[str]:
        """Names declared at module level, exported or not."""
        mod_vars: set[str] = set()
        for child in root.children:
            target_node = _exported_declaration(child)
            if target_node.type in ("lexical_declaration", "variable_declaration"):
                mod_vars.update(self._declaration_names(target_node))
        return mod_vars

    def _declaration_names(self, declaration: Any) -> list[str]:
        names: list[str] = []
        for dec in declaration.children:
            if dec.type == "variable_declarator":
                names.extend(self._declarator_names(dec))
        return names

    def function_param_props(self, fn_node: Any) -> set[str] | None:
        """Names destructured from a function's parameters, or ``None`` if none is destructured."""
        params_node = next(
            (c for c in fn_node.children if c.type in ("formal_parameters", "parameters")), None
        )
        if not params_node:
            return None
        param_props: set[str] = set()
        has_destructured_param = False
        for param in params_node.children:
            if param.type in ("(", ")", ",", ":"):
                continue
            p = _unwrap_param(param)
            if p.type == "object_pattern":
                has_destructured_param = True
                param_props.update(self.bound_names(p))
        return param_props if has_destructured_param else None

    def function_param_names(self, fn_node: Any) -> set[str]:
        for c in fn_node.children:
            if c.type in ("formal_parameters", "parameters"):
                return set(self.bound_names(c))
        single_param = fn_node.child_by_field_name("parameter")
        if single_param is not None:
            return set(self.bound_names(single_param))
        return set()

    def function_local_vars(self, fn_node: Any, inherited_vars: set[str] | None = None) -> set[str]:
        """Module names, *inherited_vars* and the function's own declarations.

        A nested callback keeps its component's locals in scope, so
        ``show && <Child />`` on component state is not read as a prop guard.
        """
        local_vars: set[str] = set(self.module_vars)
        if inherited_vars:
            local_vars.update(inherited_vars)
        self._scan_declarations(fn_node, fn_node, local_vars)
        return local_vars

    def _scan_declarations(self, n: Any, fn_node: Any, local_vars: set[str]) -> None:
        if n != fn_node and n.type in _FUNCTION_NODES:
            return
        if n.type == "variable_declarator":
            local_vars.update(self._declarator_names(n))
        for c in n.children:
            self._scan_declarations(c, fn_node, local_vars)

    # -- walk ---------------------------------------------------------------

    def walk(
        self,
        n: Any,
        current_fn: str | None = None,
        local_vars: set[str] | None = None,
        param_props: set[str] | None = None,
        function_depth: int = 0,
    ) -> None:
        if n.type in _FUNCTION_NODES:
            current_fn = self._function_name(n, current_fn)
            local_vars, param_props = self._function_scope(n, local_vars, param_props, function_depth)
            function_depth += 1
        if n.type == "binary_expression":
            self._record_and_guard(n, current_fn, local_vars, param_props)
        elif n.type == "ternary_expression":
            self._record_ternary_guard(n, current_fn, local_vars, param_props)
        for child in n.children:
            self.walk(child, current_fn, local_vars, param_props, function_depth)

    def _function_name(self, n: Any, current_fn: str | None) -> str:
        fn_name = current_fn
        if n.type in ("function_declaration", "method_definition"):
            for c in n.children:
                if c.type in ("identifier", "property_identifier"):
                    fn_name = self.text(c)
                    break
        elif n.parent and n.parent.type == "variable_declarator":
            for c in n.parent.children:
                if c.type == "identifier":
                    fn_name = self.text(c)
                    break
        return fn_name or "Anonymous"

    def _function_scope(
        self,
        n: Any,
        local_vars: set[str] | None,
        param_props: set[str] | None,
        function_depth: int,
    ) -> tuple[set[str], set[str]]:
        scope_vars = self.function_local_vars(n, local_vars)
        scope_props = set(param_props or ())
        scope_props.update(self.function_param_props(n) or ())
        if function_depth > 0:
            # A callback's own parameters shadow outer names; the component's
            # destructured props stay eligible guards.
            scope_vars.update(self.function_param_names(n))
        return scope_vars, scope_props

    def _record_and_guard(
        self, n: Any, current_fn: str | None, local_vars: set[str] | None, param_props: set[str] | None
    ) -> None:
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
            jsx_targets = self.jsx_targets(right)
            if jsx_targets and current_fn:
                self._record(current_fn, jsx_targets, self.prop_names(left, local_vars, param_props))

    def _record_ternary_guard(
        self, n: Any, current_fn: str | None, local_vars: set[str] | None, param_props: set[str] | None
    ) -> None:
        cond = None
        consequence = None
        for c in n.children:
            if c.type not in ("?", ":"):
                if cond is None:
                    cond = c
                elif consequence is None:
                    consequence = c
        if cond and consequence and current_fn:
            jsx_targets = self.jsx_targets(consequence)
            if jsx_targets:
                self._record(current_fn, jsx_targets, self.prop_names(cond, local_vars, param_props))

    def _record(self, current_fn: str, jsx_targets: list[str], prop_names: list[str]) -> None:
        for prop_name in prop_names:
            for target in jsx_targets:
                self.results.append((current_fn, target, prop_name))


def _negation(n: Any) -> tuple[bool, Any]:
    """Whether unary *n* is ``!`` and its operand (the last non-``!`` child)."""
    negated = False
    operand = None
    for c in n.children:
        if c.type == "!":
            negated = True
        else:
            operand = c
    return negated, operand


def _exported_declaration(child: Any) -> Any:
    """The declaration inside ``export const ...``, else *child* itself."""
    if child.type == "export_statement":
        for c in child.children:
            if c.type in ("lexical_declaration", "variable_declaration"):
                return c
    return child


def _unwrap_param(param: Any) -> Any:
    """The pattern inside a typed or defaulted parameter."""
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
    return p


GuardedRenders = Callable[[str], list[tuple[str, str, str]]]


def unsatisfied_prop_guard(
    graph: Any,
    guarded_renders: GuardedRenders,
    sym_id: str,
    sym_name: str,
    visited: set[str] | None = None,
    memo: dict[str, tuple[str, str] | None] | None = None,
) -> tuple[str, str] | None:
    """``(prop, parent)`` when every route to *sym_id* renders it behind a never-supplied prop.

    One unguarded or satisfied route keeps the symbol reachable, so a guard is
    reported only when every predecessor resolves to one, directly or through
    its own callers. *guarded_renders* returns a file's guarded render triples.
    """
    if not graph.has_node(sym_id):
        return None

    if visited is None:
        visited = set()
    if memo is None:
        memo = {}
    if sym_id in memo:
        return memo[sym_id]
    if sym_id in visited:
        return None
    visited.add(sym_id)

    predecessors = _use_predecessors(graph, sym_id)
    if not predecessors:
        memo[sym_id] = None
        return None

    resolved_guards: list[tuple[str, str]] = []
    for pred in predecessors:
        pred_file = pred.split("::")[0] if "::" in pred else pred
        if not pred_file.endswith((".tsx", ".jsx", ".ts", ".js")):
            continue
        guard = _blocked_guard(graph, guarded_renders(pred_file), pred, sym_name)
        if guard is not None:
            resolved_guards.append(guard)
            continue
        parent_guard = unsatisfied_prop_guard(
            graph, guarded_renders, pred, pred.split("::")[-1], set(visited), memo
        )
        if parent_guard is None:
            memo[sym_id] = None
            return None
        resolved_guards.append(parent_guard)

    result = (
        resolved_guards[0]
        if resolved_guards and len(resolved_guards) == len(predecessors)
        else None
    )
    memo[sym_id] = result
    return result


def _use_predecessors(graph: Any, node: str) -> list[str]:
    """Predecessors of *node* over a reachability-use edge."""
    return [
        pred
        for pred in graph.predecessors(node)
        if graph[pred][node].get("edge_type") in REACHABILITY_USE_EDGE_TYPES
    ]


def _blocked_guard(
    graph: Any, guarded: list[tuple[str, str, str]], pred: str, sym_name: str
) -> tuple[str, str] | None:
    """The guard *pred* renders *sym_name* behind, if no caller of *pred* supplies its prop."""
    pred_parent = pred.split("::")[-1] if "::" in pred else None
    found_guard = _matching_guard(guarded, sym_name, pred_parent)
    if found_guard is None:
        return None
    prop_name, _ = found_guard
    incoming = _use_predecessors(graph, pred)
    if incoming and all(
        _withholds(graph[caller][pred].get("supplied_props"), prop_name) for caller in incoming
    ):
        return found_guard
    return None


def _matching_guard(
    guarded: list[tuple[str, str, str]], sym_name: str, pred_parent: str | None
) -> tuple[str, str] | None:
    """``(prop, parent)`` of the first guarded render of *sym_name* inside *pred_parent*."""
    for parent_fn, child_comp, prop_name in guarded:
        if child_comp == sym_name and (
            pred_parent is None or parent_fn == pred_parent or parent_fn == "Anonymous"
        ):
            return (prop_name, parent_fn if parent_fn != "Anonymous" else (pred_parent or ""))
    return None


def _withholds(supplied: Any, prop_name: str) -> bool:
    """Whether a call site's recorded props are known and omit *prop_name*."""
    return supplied is not None and prop_name not in supplied
