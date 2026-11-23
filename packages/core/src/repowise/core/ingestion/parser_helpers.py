"""Stateless AST helper functions used by :class:`~.parser.ASTParser`.

Language-neutral tree-sitter utilities live here; per-language helpers live in
:mod:`.lang_helpers` and are re-exported so the ``parser`` package imports one module.
No imports from ``parser``, so there is no import cycle.
"""

from __future__ import annotations

import re
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
from .models import CSHARP_INTEGER_CONSTANT, TS_JS_LANGUAGES
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
    "cpp_template_type_name",
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
    **dict.fromkeys(TS_JS_LANGUAGES, _TS_JS_FUNCTION_SCOPES),
}
# Statements that can be ``import type`` / ``export type``; only these are
# asked, since a ``require`` assignment may bind a variable named ``type``.
_TS_TYPE_STATEMENTS = frozenset({"import_statement", "export_statement"})
# ``typeof import("./m")`` and its kin: erased before anything runs.
_TS_TYPE_POSITIONS = frozenset(
    {"type_query", "type_annotation", "type_alias_declaration", "interface_declaration"}
)
_TYPE_CHECKING_MODULES = frozenset({"typing", "typing_extensions"})


def _is_aliased_type_checking(root: Node, name: str, src: str) -> bool:
    """True if ``name`` is imported as ``TYPE_CHECKING`` from ``typing`` or ``typing_extensions``."""
    def _search(node: Node) -> bool:
        if node.type in ("function_definition", "class_definition"):
            return False
        if node.type == "import_from_statement":
            mod_node = node.child_by_field_name("module_name")
            if mod_node is not None and node_text(mod_node, src) in _TYPE_CHECKING_MODULES:
                for child in node.children:
                    if child.type == "aliased_import":
                        orig = child.child_by_field_name("name")
                        alias = child.child_by_field_name("alias")
                        if (
                            orig is not None
                            and node_text(orig, src) == "TYPE_CHECKING"
                            and alias is not None
                            and node_text(alias, src) == name
                        ):
                            return True
        return any(_search(child) for child in node.children)

    return _search(root)


def _is_type_checking_flag(condition: Node | None, src: str) -> bool:
    """``TYPE_CHECKING``, ``<module>.TYPE_CHECKING``, ``False``, ``0``, or an alias.

    Ceiling: anything else is runtime, so ``not``/``and``/``or`` and parentheses
    are missed on the safe side: the import stays a runtime edge.
    """
    if condition is None:
        return False
    if condition.type == "false":
        return True
    if condition.type == "integer" and node_text(condition, src).strip() == "0":
        return True
    if condition.type == "attribute":
        attr = condition.child_by_field_name("attribute")
        return (
            attr is not None
            and attr.type == "identifier"
            and node_text(attr, src) == "TYPE_CHECKING"
        )
    if condition.type != "identifier":
        return False
    name = node_text(condition, src)
    if name == "TYPE_CHECKING":
        return True
    root = condition
    while root.parent is not None:
        root = root.parent
    return _is_aliased_type_checking(root, name, src)


def _is_type_checking_branch(node: Node, child: Node, src: str) -> bool:
    """``child`` is the body of ``if``/``elif TYPE_CHECKING:``, not an ``else``."""
    if node.type not in ("if_statement", "elif_clause"):
        return False
    if child != node.child_by_field_name("consequence"):
        return False
    return _is_type_checking_flag(node.child_by_field_name("condition"), src)


def python_import_scope(stmt_node: Node) -> tuple[tuple[int, int], ...] | None:
    """The 1-based line span of the function a Python import sits in, else None.

    An import in a function body binds its names as locals of that function
    (and of functions nested in it), so the rest of the file never sees them.
    """
    node = stmt_node.parent
    while node is not None:
        if node.type == "function_definition":
            return ((node.start_point[0] + 1, node.end_point[0] + 1),)
        node = node.parent
    return None


def python_bound_module_name(module_node: Node, src: str) -> str:
    """The name ``import a.b`` binds (``a``), or its alias (``import a.b as c``)."""
    parent = module_node.parent
    if parent is not None and parent.type == "aliased_import":
        alias = parent.child_by_field_name("alias")
        if alias is not None:
            return node_text(alias, src)
    # ``dotted_name`` holds one identifier per segment; the first is bound.
    first = module_node.named_children[0] if module_node.named_children else module_node
    return node_text(first, src)


_PY_IMPORT_STATEMENTS = frozenset(
    {"import_statement", "import_from_statement", "future_import_statement"}
)


def python_load_time_names(root: Node, src: str) -> frozenset[str]:
    """Every identifier a Python module mentions while it loads.

    Module and class bodies, decorators, default values, annotations and base
    classes run at import; function and lambda bodies do not, so they are
    skipped, as are import statements, the ``.attr`` half of an attribute and
    a keyword argument's name. Ceiling: any load-time mention counts, an
    assignment or an ``if __name__ == "__main__":`` body included, so an
    import stays in the ordinary cycle tier when in doubt.
    """
    names: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        kind = node.type
        if kind == "identifier":
            names.add(node_text(node, src))
        elif kind == "attribute":
            stack.extend(c for c in (node.child_by_field_name("object"),) if c is not None)
        elif kind == "keyword_argument":
            stack.extend(c for c in (node.child_by_field_name("value"),) if c is not None)
        elif kind not in _PY_IMPORT_STATEMENTS:
            body = (
                node.child_by_field_name("body")
                if kind in ("function_definition", "lambda")
                else None
            )
            stack.extend(c for c in node.children if body is None or c != body)
    return frozenset(names)


# Handlers that let a module survive its import failing. A broader handler
# (``Exception``, ``BaseException``, bare ``except:``) usually guards the code
# after the import too, so it is not read as an import guard.
_IMPORT_FAILURES = frozenset({"ImportError", "ModuleNotFoundError"})


def _catches_import_failure(expr: Node | None, src: str) -> bool:
    """An ``except`` value naming ``ImportError`` / ``ModuleNotFoundError``,
    alone, in a tuple, as ``builtins.X``, or with ``as name``."""
    if expr is None:
        return False
    if expr.type in ("as_pattern", "parenthesized_expression"):
        return bool(expr.named_children) and _catches_import_failure(expr.named_children[0], src)
    if expr.type == "tuple":
        return any(_catches_import_failure(item, src) for item in expr.named_children)
    if expr.type == "attribute":
        expr = expr.child_by_field_name("attribute")
    return (
        expr is not None and expr.type == "identifier" and node_text(expr, src) in _IMPORT_FAILURES
    )


_EXIT_CALLS = frozenset({"sys.exit", "exit", "quit", "os._exit"})


def _ends_the_module(stmt: Node, src: str) -> bool:
    """``raise ...`` or a ``sys.exit(...)``-style call statement."""
    if stmt.type == "raise_statement":
        return True
    call = stmt.named_children[0] if stmt.type == "expression_statement" else None
    function = call.child_by_field_name("function") if call and call.type == "call" else None
    return function is not None and node_text(function, src) in _EXIT_CALLS


def _handler_lets_the_module_go_on(clause: Node, src: str) -> bool:
    """None of the handler's own statements re-raises or exits. A ``raise``
    under a nested ``if`` is not followed: the module may still survive."""
    block = next((c for c in clause.named_children if c.type == "block"), None)
    return block is None or not any(_ends_the_module(s, src) for s in block.named_children)


def _is_import_guard_body(node: Node, child: Node, src: str) -> bool:
    """``child`` is the body of a ``try`` whose import-failure handlers all let
    the module go on, not its handlers, ``else`` or ``finally``. One fatal
    handler is enough to leave it runtime: a cycle can raise the exception it
    catches (``from b import B`` on a half-built ``b`` raises ``ImportError``,
    not ``ModuleNotFoundError``)."""
    if node.type != "try_statement" or child != node.child_by_field_name("body"):
        return False
    handlers = [
        clause
        for clause in node.children
        if clause.type == "except_clause"
        and _catches_import_failure(clause.child_by_field_name("value"), src)
    ]
    return bool(handlers) and all(_handler_lets_the_module_go_on(h, src) for h in handlers)


def import_load_kind(stmt_node: Node, language: str, src: str) -> tuple[bool, bool, bool]:
    """``(type_only, deferred, guarded)`` for an import, from the statement and where it sits.

    Type-only: ``import type`` / ``export type`` (TS), under ``if
    TYPE_CHECKING:`` (Python), or in a type position such as ``typeof
    import()`` (TS), so it never runs. Deferred: inside a function or lambda
    body, so it runs on first call rather than at module load, the usual way to
    break an import cycle on purpose. Guarded (Python): in the body of a ``try``
    whose handlers catch ``ImportError``, so it runs at load but the module
    already survives it failing. Type-only wins over both, deferred over
    guarded. Reads the statement and its ancestor chain only.
    """
    scopes = _LOAD_DEFERRING_SCOPES.get(language)
    if scopes is None:
        return False, False, False
    if stmt_node.type in _TS_TYPE_STATEMENTS and is_type_only_ts_js_import(stmt_node, src):
        return True, False, False
    deferred = guarded = False
    child, node = stmt_node, stmt_node.parent
    while node is not None:
        if node.type in scopes:
            deferred = True
        elif (
            _is_type_checking_branch(node, child, src)
            if language == "python"
            else node.type in _TS_TYPE_POSITIONS
        ):
            return True, False, False
        elif language == "python" and _is_import_guard_body(node, child, src):
            guarded = True
        child, node = node, node.parent
    return False, deferred, guarded and not deferred


def _has_missing_scope_separator(qualified: Node) -> bool:
    """Is the ``::`` in this ``qualified_identifier`` a tree-sitter repair node?

    When an unexpanded macro precedes a return type on the next line,
    tree-sitter-cpp reads ``MACRO ReturnType name(...)`` as a declaration and
    repairs it with a zero-width ``(MISSING "::")``, folding the return type
    into a ``qualified_identifier`` scope. A real ``Foo::bar`` definition has
    a ``::`` child with source text, so only the repaired shape trips this.
    """
    return any(child.type == "::" and child.is_missing for child in qualified.children)


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
    # tree-sitter-cpp repairs an unexpanded macro plus a return type on the
    # following line into a qualified_identifier by inserting a zero-width
    # ``(MISSING "::")`` node, so ``LIB_ALWAYS_INLINE box_contents_t`` +
    # ``box_decode(...)`` reads as a declaration and the free function's
    # return type is mistaken for the enclosing scope. A genuine
    # ``Foo::bar`` definition carries a real ``::`` child, so skipping the
    # missing one keeps out-of-line members classified correctly.
    if _has_missing_scope_separator(parent):
        return None
    if scope.type == "template_type":
        return cpp_template_type_name(scope, src)
    text = node_text(scope, src).strip()
    # ``scope`` may itself be a qualified path (``NS::Foo``); take the
    # last component — that's the immediate enclosing type.
    return text.rsplit("::", 1)[-1] or None


_CPP_SPACE_RUN = re.compile(r"\s+")
_CPP_BRACKET_SPACE = re.compile(r"(?<=<) | (?=>)")


def cpp_template_type_name(node: Node, src: str) -> str:
    """The class a C++ ``Name<Args>`` scope or class head names.

    A member of the primary template, defined out of line, spells the
    template's own parameters (``parse_context<Char>::f``) and belongs to
    ``parse_context``, where a ``ctx.f()`` receiver lookup finds it. Any other
    argument list is an explicit or partial specialization, a class of its
    own: it keeps its arguments, on one line, so its members never merge with
    the primary's.
    """
    name = node.child_by_field_name("name")
    arguments = node.child_by_field_name("arguments")
    if name is None or arguments is None:
        return _CPP_SPACE_RUN.sub(" ", node_text(node, src).strip())
    if _spells_enclosing_parameters(node, arguments, src):
        return node_text(name, src)
    return _CPP_BRACKET_SPACE.sub("", _CPP_SPACE_RUN.sub(" ", node_text(node, src).strip()))


def _spells_enclosing_parameters(node: Node, arguments: Node, src: str) -> bool:
    """Do *arguments* name an enclosing ``template <...>``'s parameters, in order?"""
    spelled = [
        node_text(arg, src).replace(" ", "").removesuffix("...") for arg in arguments.named_children
    ]
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "template_declaration":
            parameters = ancestor.child_by_field_name("parameters")
            if parameters is not None and spelled == _template_parameter_names(parameters, src):
                return True
        ancestor = ancestor.parent
    return False


def _template_parameter_names(parameters: Node, src: str) -> list[str | None]:
    """``template <typename T, int N, typename... Ts>`` -> ``["T", "N", "Ts"]``."""
    names: list[str | None] = []
    for parameter in parameters.named_children:
        # ``template <typename> class C`` names C in its trailing ``class C``.
        if parameter.type == "template_template_parameter_declaration":
            parameter = parameter.named_children[-1]
        name = parameter.child_by_field_name("name") or parameter.child_by_field_name("declarator")
        if name is None:
            name = next((c for c in parameter.named_children if c.type in ("type_identifier", "identifier")), None)
        names.append(node_text(name, src).lstrip("*&. ") if name is not None else None)
    return names


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
    """A call's argument expressions, for a language whose overloads read them (Java, Apex, Kotlin).

    Read from the argument list already in hand, the way ``_count_arguments``
    counts it.
    """
    if language == "kotlin":
        return _kotlin_positional_arguments(arg_nodes)
    if language not in ("java", "apex", "csharp") or not arg_nodes:
        return []
    skip_types = frozenset({"(", ")", ","})
    arguments = [
        c for c in arg_nodes[0].children if c.type not in skip_types and "comment" not in c.type
    ]
    if language == "csharp":
        # A named argument (``onRetry: f``) binds by name, so no position is known.
        if any(a.child_by_field_name("name") is not None for a in arguments):
            return []
        return [a.named_children[-1] if a.named_children else a for a in arguments]
    return arguments


def _kotlin_positional_arguments(arg_nodes: list[Node]) -> list[Node]:
    """Each Kotlin argument's expression, in order, or none at all.

    A named or spread argument, or a trailing lambda, takes a parameter its
    position does not say, so such a call is not read.
    """
    arguments = arg_nodes[0] if arg_nodes else None
    if arguments is None or arguments.type != "value_arguments" or _kotlin_trailing_lambda(arguments):
        return []
    found = []
    for argument in arguments.named_children:
        if argument.type != "value_argument":
            continue
        parts = argument.named_children
        if len(parts) != 1 or parts[0].type == "spread_expression":
            return []
        found.append(parts[0])
    return found


# Kotlin literals whose type no expected type changes; an unsuffixed integer
# is an ``Int`` only where an ``Int`` overload exists, which is the one asked.
_KOTLIN_LITERAL_TYPES = {"string_literal": "String", "character_literal": "Char"}
_KOTLIN_BOOLEANS = frozenset({"true", "false"})
# Kotlin's grammar reads these keywords as identifiers; they name no variable.
_NOT_NAMES = frozenset({b"true", b"false", b"null", b"this"})


def _kotlin_literal_type(node: Node, src: str) -> str | None:
    text = node_text(node, src)
    if node.type == "identifier":
        return "Boolean" if text in _KOTLIN_BOOLEANS else None
    if node.type != "number_literal":
        return _KOTLIN_LITERAL_TYPES.get(node.type)
    return _kotlin_number_type(text.lower().replace("_", ""))


def _kotlin_number_type(lowered: str) -> str | None:
    """``Long``, ``Int``, ``Float`` or ``Double`` for a lowercased number.

    None when unsigned, and for an unsuffixed integer past ``Int``'s range,
    which Kotlin types as a ``Long`` only where no ``Int`` is expected.
    """
    if lowered.endswith(("u", "ul")):
        return None
    if lowered.endswith("l"):
        return "Long"
    base = {"0x": 16, "0b": 2}.get(lowered[:2], 10)
    if base == 10 and (fraction := _kotlin_fraction_type(lowered)) is not None:
        return fraction
    try:
        value = int(lowered[2:] if base != 10 else lowered, base)
    except ValueError:
        return None
    return "Int" if value < 2**31 else None


def _kotlin_fraction_type(lowered: str) -> str | None:
    if lowered.endswith("f"):
        return "Float"
    return "Double" if "." in lowered or "e" in lowered else None


def _argument_literal_types(
    arguments: list[Node], src: str, language: str = "java"
) -> tuple[str | None, ...] | None:
    """The type of each literal argument, None where an argument is no literal.

    None overall when no argument is a literal. C# reports a lambda's and a
    construction's shape instead (``_csharp_argument_shape``).
    """
    literal_type = {
        "apex": _apex_literal_type,
        "csharp": _csharp_argument_shape,
        "kotlin": _kotlin_literal_type,
    }.get(language, _java_literal_type)
    types = tuple(literal_type(argument, src) for argument in arguments)
    return types if any(types) else None


def _csharp_argument_shape(node: Node, src: str) -> str | None:
    """``=>2`` for a two-parameter lambda (``=>`` when unwritten), ``new Foo`` for a construction."""
    if node.type == "lambda_expression":
        parameters = node.child_by_field_name("parameters")
        if parameters is not None and parameters.type == "implicit_parameter":
            return "=>1"
        return f"=>{len(parameters.named_children)}" if parameters is not None else "=>"
    if node.type == "anonymous_method_expression":
        parameters = next((c for c in node.named_children if c.type == "parameter_list"), None)
        return "=>" if parameters is None else f"=>{len(parameters.named_children)}"
    if node.type == "object_creation_expression":
        created = node.child_by_field_name("type")
        if created is not None and created.type == "generic_name":
            created = created.named_children[0] if created.named_children else None
        if created is not None and created.type == "identifier":
            return f"new {node_text(created, src)}"
    return _csharp_literal_type(node, src)


# C# literal nodes whose type no target type changes.
_CSHARP_LITERAL_TYPES = {
    "string_literal": "string",
    "verbatim_string_literal": "string",
    "raw_string_literal": "string",
    "interpolated_string_expression": "string",
    "character_literal": "char",
    "boolean_literal": "bool",
}
# Integer suffixes whose constant reaches exactly what a value of the type does.
# An ``L`` constant is left untyped: when non-negative it also reaches ``ulong``.
_CSHARP_INTEGER_SUFFIXES = {"": CSHARP_INTEGER_CONSTANT, "u": "uint", "ul": "ulong", "lu": "ulong"}


def _csharp_literal_type(node: Node, src: str) -> str | None:
    if node.type == "integer_literal":
        suffix = node_text(node, src).lower().lstrip("0123456789abcdefx_")
        return _CSHARP_INTEGER_SUFFIXES.get(suffix)
    if node.type == "real_literal":
        return {"f": "float", "m": "decimal"}.get(node_text(node, src)[-1:].lower(), "double")
    return _CSHARP_LITERAL_TYPES.get(node.type)


def _argument_names(arguments: list[Node], src: str) -> tuple[str | None, ...] | None:
    """Each argument that is a bare name (a local, parameter or field), else None.

    None overall when no argument is one. The resolver reads their declared types.
    """
    names = tuple(
        node_text(a, src) if a.type == "identifier" and a.text not in _NOT_NAMES else None
        for a in arguments
    )
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
    return _java_expression_type(node, src)


def _java_expression_type(node: Node, src: str) -> str | None:
    """A class literal, a String concatenation or a lambda's shape; else a plain literal."""
    if node.type == "class_literal":
        return "Class"
    if node.type == "binary_expression":
        return _java_concatenation_type(node, src)
    if node.type == "lambda_expression":
        return _java_lambda_shape(node)
    return _JAVA_LITERAL_TYPES.get(node.type)


def _java_concatenation_type(node: Node, src: str) -> str | None:
    """``"id " + id`` is a String: a ``+`` with a String literal operand."""
    operator = node.child_by_field_name("operator")
    if operator is None or node_text(operator, src) != "+":
        return None
    operands = (node.child_by_field_name("left"), node.child_by_field_name("right"))
    if any(o is not None and _java_literal_type(o, src) == "String" for o in operands):
        return "String"
    return None


# Expression bodies that suit a void and a value-returning interface alike.
_JAVA_EITHER_BODIES = frozenset(
    {"assignment_expression", "update_expression", "object_creation_expression"}
)


def _java_lambda_shape(node: Node) -> str | None:
    """A lambda as ``->N:value``, ``->N:void`` or ``->N:either``, N its parameter count.

    Read by overload choice against functional interfaces (``Runnable`` vs
    ``Supplier``). A body that is a bare method call may or may not yield a
    value, and a block ending in a throw fits both, so neither is judged.
    """
    body = node.child_by_field_name("body")
    if body is None or body.type == "method_invocation":
        return None
    count = _lambda_parameter_count(node)
    if body.type in _JAVA_EITHER_BODIES:
        return f"->{count}:either"
    if body.type != "block":
        return f"->{count}:value"
    returns = _block_returns(body)
    if not returns and _ends_in_throw(body):
        return None
    return f"->{count}:{'value' if any(returns) else 'void'}"


def _lambda_parameter_count(node: Node) -> int:
    params = node.child_by_field_name("parameters")
    if params is None:
        return 0
    if params.type == "identifier":
        return 1
    return sum(1 for c in params.named_children if "comment" not in c.type)


def _ends_in_throw(block: Node) -> bool:
    statements = [c for c in block.named_children if "comment" not in c.type]
    return bool(statements) and statements[-1].type == "throw_statement"


def _block_returns(block: Node) -> list[bool]:
    """Whether each ``return`` in a lambda's own block carries a value.

    A nested lambda's or class body's returns belong to it, not this lambda.
    """
    found: list[bool] = []
    stack = list(block.named_children)
    while stack:
        node = stack.pop()
        if node.type == "return_statement":
            found.append(node.named_child_count > 0)
        elif node.type not in ("lambda_expression", "class_body"):
            stack.extend(node.named_children)
    return found


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
