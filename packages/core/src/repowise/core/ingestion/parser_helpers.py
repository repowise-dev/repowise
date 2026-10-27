"""Stateless AST helper functions used by :class:`~.parser.ASTParser`.

Language-neutral tree-sitter utilities live here; per-language helpers live in
:mod:`.lang_helpers` and are re-exported so the ``parser`` package imports one module.
No imports from ``parser``, so there is no import cycle.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from tree_sitter import Node

from .extractors import node_text
from .extractors.bindings.ts_js import is_type_only_ts_js_import
from .lang_helpers.anonymous_classes import (
    follow_renamed_hosts,
    in_anonymous_class,
    is_anonymous_member,
    scope_segment,
)
from .lang_helpers.elixir import (
    _elixir_call_is_definitional,
    _elixir_is_template_definition,
    _elixir_module_parent,
    _elixir_symbol_name,
)
from .lang_helpers.fsharp import (
    _fsharp_binding_end_line,
    _fsharp_binding_has_params,
    _fsharp_binding_is_nested,
    _fsharp_parent_is_type,
    _fsharp_parent_name,
)
from .lang_helpers.objc import (
    _dedupe_objc_interface_symbols,
    _objc_call_is_block_variable,
    _objc_container_node,
    _objc_container_parent,
    _objc_is_macro_enum,
    _objc_message_selector,
    _objc_symbol_name,
)
from .lang_helpers.pascal import _dedupe_pascal_interface_symbols, _qualified_pascal_parent
from .lang_helpers.source_prep import (
    prepare_apex_source,
    prepare_objectivec_source,
    prepare_pascal_source,
)
from .lang_helpers.ts_js import _ts_nested_object_method_owner
from .lang_helpers.type_heads import (
    TYPE_HEAD_EXTRACTORS,
    _classify_param_origin,
    _head_type_identifier,
    _rust_head_type_identifier,
    _rust_shadowed_by_type_param,
)
from .type_names import kotlin_extension_receiver

log = structlog.get_logger(__name__)

__all__ = [
    "TYPE_HEAD_EXTRACTORS",
    "_build_qualified_name",
    "_classify_param_origin",
    "_collect_error_nodes",
    "_count_arguments",
    "_dedupe_objc_interface_symbols",
    "_dedupe_pascal_interface_symbols",
    "_elixir_call_is_definitional",
    "_elixir_is_template_definition",
    "_elixir_module_parent",
    "_elixir_symbol_name",
    "_find_enclosing_symbol",
    "_fsharp_binding_end_line",
    "_fsharp_binding_has_params",
    "_fsharp_binding_is_nested",
    "_fsharp_parent_is_type",
    "_fsharp_parent_name",
    "_has_callable_ancestor",
    "_head_type_identifier",
    "_is_async_node",
    "_objc_call_is_block_variable",
    "_objc_container_node",
    "_objc_container_parent",
    "_objc_is_macro_enum",
    "_objc_message_selector",
    "_objc_symbol_name",
    "_qualified_cpp_parent",
    "_qualified_pascal_parent",
    "_run_query",
    "_rust_head_type_identifier",
    "_rust_shadowed_by_type_param",
    "_ts_nested_object_method_owner",
    "follow_renamed_hosts",
    "import_load_kind",
    "in_anonymous_class",
    "is_anonymous_member",
    "prepare_apex_source",
    "prepare_objectivec_source",
    "prepare_pascal_source",
    "scope_segment",
]


def _run_query(query: object, root_node: Node) -> list[dict[str, list[Node]]]:
    """Execute a tree-sitter query and return a list of capture dicts."""
    results: list[dict[str, list[Node]]] = []
    try:
        _append_cursor_captures(query, root_node, results)
    except Exception:
        # Older py-tree-sitter has no QueryCursor; matches collected before a
        # failure are kept and the legacy API appends after them.
        _append_legacy_captures(query, root_node, results)
    return results


def _append_cursor_captures(
    query: object, root_node: Node, results: list[dict[str, list[Node]]]
) -> None:
    from tree_sitter import QueryCursor  # type: ignore[attr-defined]

    cursor = QueryCursor(query)  # type: ignore[call-arg]
    for match in cursor.matches(root_node):
        if hasattr(match, "captures"):
            results.append(match.captures)
        elif isinstance(match, tuple) and len(match) == 2:
            results.append(match[1])


def _append_legacy_captures(
    query: object, root_node: Node, results: list[dict[str, list[Node]]]
) -> None:
    try:
        for item in query.matches(root_node):  # type: ignore[attr-defined]
            if isinstance(item, tuple) and len(item) == 2:
                results.append(item[1])
    except Exception as exc:
        log.warning("query.matches() failed", error=str(exc))


def _collect_error_nodes(root: Node) -> list[str]:
    """Return error descriptions for any ERROR nodes in the tree."""
    errors: list[str] = []

    def _walk(node: Node) -> None:
        if node.type == "ERROR":
            errors.append(f"Parse error at line {node.start_point[0] + 1}")
        for child in node.children:
            _walk(child)

    _walk(root)
    return errors


def _is_async_node(node: Node, src: str) -> bool:
    return node.type == "async_function_definition" or any(c.type == "async" for c in node.children)


_CALLABLE_KINDS: frozenset[str] = frozenset({"function", "method"})


def _has_callable_ancestor(
    node: Node,
    symbol_kinds: dict[str, str],
    ignored_node_ids: frozenset[int] = frozenset(),
) -> bool:
    """True if ``node`` has any function/method ancestor in the AST.

    Used to filter out helpers defined inside another function's body
    (React event handlers, async-method-local coroutines, JS closures)
    from the top-level symbol list. Class bodies don't count — methods
    inside classes have only a ``class`` ancestor before the module root.

    ``ignored_node_ids`` covers grammar-recovery nodes that a language has
    positively identified as type or namespace containers rather than callables.
    """
    ancestor = node.parent
    while ancestor is not None:
        if (
            ancestor.id not in ignored_node_ids
            and symbol_kinds.get(ancestor.type) in _CALLABLE_KINDS
        ):
            return True
        ancestor = ancestor.parent
    return False


# Scopes whose body runs when called, not when the module loads. Its own set,
# not ``symbol_node_types``: a TS ``lexical_declaration`` is a symbol kind, yet
# ``const m = await import("./m")`` at the top level still runs at load.
_TS_JS_FUNCTION_SCOPES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "generator_function",
        "function",
        "arrow_function",
        "method_definition",
    }
)
_LOAD_DEFERRING_SCOPES: dict[str, frozenset[str]] = {
    "python": frozenset({"function_definition", "lambda"}),
    **dict.fromkeys(("typescript", "javascript", "svelte", "vue"), _TS_JS_FUNCTION_SCOPES),
}
# Statements that can be ``import type`` / ``export type``; only these are
# asked, since a ``require`` assignment may bind a variable named ``type``.
_TS_TYPE_STATEMENTS = frozenset({"import_statement", "export_statement"})
# ``typeof import("./m")`` and its kin: erased before anything runs.
_TS_TYPE_POSITIONS = frozenset(
    {"type_query", "type_annotation", "type_alias_declaration", "interface_declaration"}
)


def _is_type_checking_flag(condition: Node | None, src: str) -> bool:
    """``TYPE_CHECKING`` or ``<module>.TYPE_CHECKING``, matched on the node.

    Ceiling: anything else is runtime, so ``not``/``and``/``or``, parentheses
    and an alias (``from typing import TYPE_CHECKING as TC``) are missed on
    the safe side: the import stays a runtime edge.
    """
    if condition is None:
        return False
    if condition.type == "attribute":
        condition = condition.child_by_field_name("attribute")
    return (
        condition is not None
        and condition.type == "identifier"
        and node_text(condition, src) == "TYPE_CHECKING"
    )


def _is_type_checking_branch(node: Node, child: Node, src: str) -> bool:
    """``child`` is the body of ``if``/``elif TYPE_CHECKING:``, not an ``else``."""
    if node.type not in ("if_statement", "elif_clause"):
        return False
    if child != node.child_by_field_name("consequence"):
        return False
    return _is_type_checking_flag(node.child_by_field_name("condition"), src)


def import_load_kind(stmt_node: Node, language: str, src: str) -> tuple[bool, bool]:
    """``(type_only, deferred)`` for an import, from the statement and where it sits.

    Type-only: ``import type`` / ``export type`` (TS), under ``if
    TYPE_CHECKING:`` (Python), or in a type position such as ``typeof
    import()`` (TS), so it never runs. Deferred: inside a function or lambda
    body, so it runs on first call rather than at module load, the usual way to
    break an import cycle on purpose. Type-only wins when both hold. Reads the
    statement and its ancestor chain only.
    """
    scopes = _LOAD_DEFERRING_SCOPES.get(language)
    if scopes is None:
        return False, False
    if stmt_node.type in _TS_TYPE_STATEMENTS and is_type_only_ts_js_import(stmt_node, src):
        return True, False
    deferred = False
    child, node = stmt_node, stmt_node.parent
    while node is not None:
        if node.type in scopes:
            deferred = True
        elif (
            _is_type_checking_branch(node, child, src)
            if language == "python"
            else node.type in _TS_TYPE_POSITIONS
        ):
            return True, False
        child, node = node, node.parent
    return False, deferred


def _qualified_cpp_parent(name_node: Node, src: str) -> str | None:
    """Return the parent class for a C/C++ ``Class::method`` definition.

    The captured ``@symbol.name`` for a qualified function definition
    is the bare ``method`` identifier whose parent is a
    ``qualified_identifier`` carrying the class / namespace as its
    ``scope`` field. For multi-level qualifications (``NS::Foo::method``)
    the relevant parent is still the innermost qualifier — namespaces
    above it are not the symbol's containing type. Tree-sitter-cpp
    represents this by nesting ``qualified_identifier`` left-recursively,
    so the immediate parent's ``scope`` is always the right answer.

    Returns ``None`` when the name node is not inside a qualified
    identifier (i.e. plain free function).
    """
    parent = name_node.parent
    if parent is None or parent.type != "qualified_identifier":
        return None
    scope = parent.child_by_field_name("scope")
    if scope is None:
        return None
    text = node_text(scope, src).strip()
    # ``scope`` may itself be a qualified path (``NS::Foo``); take the
    # last component — that's the immediate enclosing type.
    return text.rsplit("::", 1)[-1] or None


def _build_qualified_name(file_path: str, parent_name: str | None, name: str) -> str:
    module = Path(file_path).with_suffix("").as_posix().replace("/", ".")
    if parent_name:
        return f"{module}.{parent_name.replace('::', '.')}.{name}"
    return f"{module}.{name}"


_CSHARP_TYPE_NODES = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "struct_declaration",
        "record_declaration",
        "delegate_declaration",
    }
)


def _csharp_type_parameter_count(def_node: Node) -> int | None:
    """How many type parameters a C# type declares (0 if none); None for a non-type."""
    if def_node.type not in _CSHARP_TYPE_NODES:
        return None
    for child in def_node.children:
        if child.type == "type_parameter_list":
            return sum(1 for param in child.children if param.type == "type_parameter")
    return 0


def _kotlin_receiver_fields(def_node: Node, params: list[Node], language: str, name: str) -> dict:
    """``Symbol`` fields for the receiver a Kotlin extension function declares; {} otherwise.

    Read off the header alone, up to the parameter list: the body cannot hold
    the receiver, and a long one is not worth scanning.
    """
    if language != "kotlin" or def_node.type != "function_declaration" or not params:
        return {}
    header = (def_node.text or b"")[: params[0].start_byte - def_node.start_byte]
    receiver = kotlin_extension_receiver(header.decode("utf-8", errors="replace") + "(", name)
    if receiver is None:
        return {}
    return {"written_receiver": receiver[0], "extension_receiver": receiver[1]}


def _count_arguments(arg_node: Node) -> int:
    """Count the number of arguments in an argument/argument_list node.

    Comments are children of the argument list, so an argument annotated with
    a trailing ``// name`` counted twice. Grammars spell the node type several
    ways (``comment``, ``line_comment``, ``block_comment``), hence the
    substring test rather than a fixed set.
    """
    if arg_node.type == "annotated_lambda":
        # Kotlin ``foo { .. }``: the lambda is the only argument.
        return 1
    skip_types = frozenset({"(", ")", ",", "[", "]"})
    listed = sum(
        1
        for child in arg_node.children
        if child.type not in skip_types and "comment" not in child.type
    )
    return listed + _kotlin_trailing_lambda(arg_node)


def _kotlin_trailing_lambda(arg_node: Node) -> int:
    """1 when a Kotlin call passes a trailing lambda, ``f(a) { .. }``, else 0.

    The grammar hangs the lambda off an outer ``call_expression`` wrapping the
    call, outside the argument list.
    """
    call = arg_node.parent
    outer = call.parent if call is not None else None
    if outer is None or outer.type != "call_expression" or outer.child(0) != call:
        return 0
    return int(any(child.type == "annotated_lambda" for child in outer.children))


# Java literal nodes and the type each one has, for the overload tie-break.
_JAVA_LITERAL_TYPES = {
    "true": "boolean",
    "false": "boolean",
    "character_literal": "char",
    "string_literal": "String",
    "null_literal": "null",
}
_JAVA_INTEGER_LITERALS = frozenset(
    {"decimal_integer_literal", "hex_integer_literal", "octal_integer_literal", "binary_integer_literal"}
)
_JAVA_FLOATING_LITERALS = frozenset({"decimal_floating_point_literal", "hex_floating_point_literal"})


# Apex literal nodes and the type each one has; ``null`` matches no exact type.
_APEX_LITERAL_TYPES = {
    "string_literal": "String",
    "boolean": "Boolean",
    "decimal_floating_point_literal": "Decimal",
}


def _overload_arguments(language: str, arg_nodes: list[Node]) -> list[Node]:
    """A call's argument expressions, for a language whose overloads read them (Java, Apex).

    Read from the argument list already in hand, the way ``_count_arguments``
    counts it.
    """
    if language not in ("java", "apex") or not arg_nodes:
        return []
    skip_types = frozenset({"(", ")", ","})
    return [
        c for c in arg_nodes[0].children if c.type not in skip_types and "comment" not in c.type
    ]


def _argument_literal_types(
    arguments: list[Node], src: str, language: str = "java"
) -> tuple[str | None, ...] | None:
    """The type of each literal argument, None where an argument is no literal.

    None overall when no argument is a literal.
    """
    literal_type = _apex_literal_type if language == "apex" else _java_literal_type
    types = tuple(literal_type(argument, src) for argument in arguments)
    return types if any(types) else None


def _argument_names(arguments: list[Node], src: str) -> tuple[str | None, ...] | None:
    """Each argument that is a bare name (a local, parameter or field), else None.

    None overall when no argument is one. The resolver reads their declared types.
    """
    names = tuple(node_text(a, src) if a.type == "identifier" else None for a in arguments)
    return names if any(names) else None


# A unary operator on a literal, by operand type: ``-'a'`` promotes to int.
_JAVA_SIGN_TYPES = {"char": "int", "int": "int", "long": "long", "float": "float", "double": "double"}
_JAVA_UNARY_TYPES: dict[str, dict[str, str]] = {
    "-": _JAVA_SIGN_TYPES,
    "+": _JAVA_SIGN_TYPES,
    "~": {"char": "int", "int": "int", "long": "long"},
    "!": {"boolean": "boolean"},
}


def _java_literal_type(node: Node, src: str) -> str | None:
    if node.type == "unary_expression":
        operator = node.child_by_field_name("operator")
        operand = node.child_by_field_name("operand")
        if operator is None or operand is None:
            return None
        literal = _java_literal_type(operand, src)
        return _JAVA_UNARY_TYPES.get(node_text(operator, src), {}).get(literal or "")
    if node.type in _JAVA_INTEGER_LITERALS:
        return "long" if node_text(node, src).endswith(("l", "L")) else "int"
    if node.type in _JAVA_FLOATING_LITERALS:
        return "float" if node_text(node, src).endswith(("f", "F")) else "double"
    return _JAVA_LITERAL_TYPES.get(node.type)


def _apex_literal_type(node: Node, src: str) -> str | None:
    if node.type == "int":
        return "Long" if node_text(node, src).endswith(("l", "L")) else "Integer"
    return _APEX_LITERAL_TYPES.get(node.type)


def _find_enclosing_symbol(
    line: int,
    symbol_ranges: list[tuple[int, int, str]],
) -> str | None:
    """Find the innermost symbol whose line range contains *line*."""
    best_id: str | None = None
    best_span = float("inf")

    for start, end, sym_id in symbol_ranges:
        if start > line:
            break
        if start <= line <= end:
            span = end - start
            if span < best_span:
                best_span = span
                best_id = sym_id

    return best_id
