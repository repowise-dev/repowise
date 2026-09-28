"""Stateless AST helper functions used by :class:`~.parser.ASTParser`.

Language-neutral tree-sitter utilities live here; per-language helpers live in
:mod:`.lang_helpers` and are re-exported so ``parser.py`` imports one module.
No imports from ``parser``, so there is no import cycle.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from tree_sitter import Node

from .extractors import node_text
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
from .lang_helpers.source_prep import prepare_objectivec_source, prepare_pascal_source
from .lang_helpers.type_heads import (
    TYPE_HEAD_EXTRACTORS,
    _classify_param_origin,
    _head_type_identifier,
    _rust_head_type_identifier,
    _rust_shadowed_by_type_param,
)

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
    "prepare_objectivec_source",
    "prepare_pascal_source",
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

    ``ignored_node_ids`` covers grammar-recovery nodes that a language query
    has positively identified as type containers rather than callables.
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
        return f"{module}.{parent_name}.{name}"
    return f"{module}.{name}"


def _count_arguments(arg_node: Node) -> int:
    """Count the number of arguments in an argument/argument_list node.

    Comments are children of the argument list, so an argument annotated with
    a trailing ``// name`` counted twice. Grammars spell the node type several
    ways (``comment``, ``line_comment``, ``block_comment``), hence the
    substring test rather than a fixed set.
    """
    skip_types = frozenset({"(", ")", ",", "[", "]"})
    return sum(
        1
        for child in arg_node.children
        if child.type not in skip_types and "comment" not in child.type
    )


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
