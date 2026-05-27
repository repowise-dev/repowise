"""Stateless AST helper functions used by :class:`~.parser.ASTParser`.

Pure tree-sitter node utilities (query execution, qualified-name building,
C# type-head extraction, call-argument counting, enclosing-symbol lookup,
…) extracted from ``parser.py`` so that module holds the parser class and
this one holds the free functions it calls. No state, no imports from
``parser`` — keeping this a leaf so there is no import cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import structlog
from tree_sitter import Node

from .extractors import node_text

log = structlog.get_logger(__name__)

# Private alias for internal use (mirrors the one in parser.py)
_node_text = node_text


def _run_query(query: object, root_node: Node) -> list[dict[str, list[Node]]]:
    """Execute a tree-sitter query and return a list of capture dicts."""
    results: list[dict[str, list[Node]]] = []
    try:
        from tree_sitter import QueryCursor  # type: ignore[attr-defined]

        cursor = QueryCursor(query)  # type: ignore[call-arg]
        for match in cursor.matches(root_node):
            if hasattr(match, "captures"):
                results.append(match.captures)
            elif isinstance(match, tuple) and len(match) == 2:
                _, caps = match
                results.append(caps)
    except Exception:
        try:
            for item in query.matches(root_node):  # type: ignore[attr-defined]
                if isinstance(item, tuple) and len(item) == 2:
                    _, caps = item
                    results.append(caps)
        except Exception as exc:
            log.warning("query.matches() failed", error=str(exc))
    return results


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


def _has_callable_ancestor(node: Node, symbol_kinds: dict[str, str]) -> bool:
    """True if ``node`` has any function/method ancestor in the AST.

    Used to filter out helpers defined inside another function's body
    (React event handlers, async-method-local coroutines, JS closures)
    from the top-level symbol list. Class bodies don't count — methods
    inside classes have only a ``class`` ancestor before the module root.
    """
    ancestor = node.parent
    while ancestor is not None:
        if symbol_kinds.get(ancestor.type) in _CALLABLE_KINDS:
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
    text = src[scope.start_byte : scope.end_byte].strip()
    # ``scope`` may itself be a qualified path (``NS::Foo``); take the
    # last component — that's the immediate enclosing type.
    return text.rsplit("::", 1)[-1] or None


def _build_qualified_name(file_path: str, parent_name: str | None, name: str) -> str:
    module = Path(file_path).with_suffix("").as_posix().replace("/", ".")
    if parent_name:
        return f"{module}.{parent_name}.{name}"
    return f"{module}.{name}"


# ---------------------------------------------------------------------------
# Type reference helpers (used by _extract_type_refs)
# ---------------------------------------------------------------------------

# Type expressions that never resolve to a user-defined .NET type. Skipping
# these here avoids polluting the resolver with hopeless lookups. Generic
# args inside `IList<T>` are stripped before this check is applied.
_BUILTIN_CSHARP_TYPES: frozenset[str] = frozenset(
    {
        "void",
        "bool",
        "byte",
        "sbyte",
        "char",
        "short",
        "ushort",
        "int",
        "uint",
        "long",
        "ulong",
        "float",
        "double",
        "decimal",
        "string",
        "object",
        "nint",
        "nuint",
        "dynamic",
        "var",
        # Frequently appearing BCL types that are always external — listing
        # them here is purely a performance optimisation (one dict miss
        # avoided per occurrence).
        "Task",
        "ValueTask",
        "CancellationToken",
        "Action",
        "Func",
        "Type",
        "Exception",
        "DateTime",
        "DateTimeOffset",
        "TimeSpan",
        "Guid",
        "Uri",
        "Stream",
    }
)

_PARAM_ORIGIN_BY_ANCESTOR: dict[str, str] = {
    "constructor_declaration": "ctor_param",
    "method_declaration": "method_param",
    "delegate_declaration": "delegate_param",
    "record_declaration": "ctor_param",
    "class_declaration": "ctor_param",
    "struct_declaration": "ctor_param",
    # Go type positions — node types are Go-only so they never collide with
    # the C# entries above. The origin is provenance only; resolution treats
    # all type-use edges equally.
    "field_declaration": "field_type",
    "parameter_declaration": "param_type",
    "composite_literal": "composite_literal",
}


def _head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a C# type expression, or None.

    Examples:
        ``IBasketService``                  → "IBasketService"
        ``IList<Basket>``                   → "IList"
        ``Acme.Catalog.IRepository<T>``     → "IRepository"
        ``ref readonly Span<byte>``         → "Span"
        ``string``                          → None (built-in)
        ``int?``                            → None
        ``T``                               → None (likely a generic param)

    The point of returning the head identifier is that the
    DotNetProjectIndex type-name lookup is keyed by unqualified type
    name. Generic-arg recursion is intentionally NOT done here — each
    generic arg is captured in its own ``@param.type`` if it's a real
    parameter type, and the resolver doesn't currently track generic
    instantiation graphs.
    """
    head_node: Node | None = type_node

    # Unwrap modifier wrappers: nullable_type, ref_type, pointer_type,
    # array_type, tuple_type. tree-sitter-c-sharp puts the inner type
    # at field "type" or as the first non-trivia child.
    for _ in range(6):
        if head_node is None:
            return None
        if head_node.type in ("nullable_type", "ref_type", "pointer_type", "array_type"):
            inner = head_node.child_by_field_name("type")
            if inner is None:
                # Fall back to first identifier-bearing child
                inner = next(
                    (
                        c
                        for c in head_node.children
                        if c.type not in (",", "?", "*", "&", "ref", "out", "in", "[", "]")
                    ),
                    None,
                )
            head_node = inner
            continue
        break

    if head_node is None:
        return None

    if head_node.type == "identifier":
        text = _node_text(head_node, src)
    elif head_node.type == "predefined_type":
        text = _node_text(head_node, src)
    elif head_node.type == "generic_name":
        name_child = head_node.child_by_field_name("name") or next(
            (c for c in head_node.children if c.type == "identifier"),
            None,
        )
        text = _node_text(name_child, src) if name_child else ""
    elif head_node.type == "qualified_name":
        # `Foo.Bar.Baz` — take the rightmost identifier
        idents = [c for c in head_node.children if c.type == "identifier"]
        text = _node_text(idents[-1], src) if idents else ""
    elif head_node.type == "tuple_type":
        return None  # Tuple elements aren't single types
    else:
        # Unknown shape — fall back to first identifier in the subtree
        ident = _first_descendant(head_node, "identifier")
        text = _node_text(ident, src) if ident else ""

    if not text or not text[0].isalpha() and text[0] != "_":
        return None
    if text in _BUILTIN_CSHARP_TYPES:
        return None
    # Single-uppercase-letter heads are overwhelmingly generic params (T, K, V).
    # Skipping them avoids spurious lookups against a type-name index that
    # would never contain them.
    if len(text) == 1 and text.isupper():
        return None
    return text


def _first_descendant(node: Node, type_name: str) -> Node | None:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == type_name:
            return current
        stack.extend(current.children)
    return None


def _classify_param_origin(type_node: Node) -> str:
    """Walk up to find the enclosing declaration and map to an origin tag.

    The walk stops at the first matching ancestor or after a small depth
    cap. Falling off the cap means the capture was outside a recognised
    declaration shape (shouldn't happen given the query patterns, but
    guards against grammar drift); we tag those ``method_param``.
    """
    cur: Node | None = type_node
    for _ in range(8):
        if cur is None:
            break
        origin = _PARAM_ORIGIN_BY_ANCESTOR.get(cur.type)
        if origin is not None:
            return origin
        cur = cur.parent
    return "method_param"


# ---------------------------------------------------------------------------
# Go type-reference head extraction
# ---------------------------------------------------------------------------

# Predeclared Go type names — never resolve to a user-defined type, so they
# are dropped before the resolver lookup. ``error``/``any``/``comparable``
# are predeclared identifiers, not keywords, but behave as builtins here.
_GO_BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        "string", "bool", "byte", "rune", "error", "any", "comparable",
        "int", "int8", "int16", "int32", "int64",
        "uint", "uint8", "uint16", "uint32", "uint64", "uintptr",
        "float32", "float64", "complex64", "complex128",
    }
)


def _go_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head type name of a Go type expression, or None.

    Unwraps the modifier shells Go layers around a named type so the
    resolver sees the bare identifier it indexes by:

        ``Options``                 → "Options"
        ``*Cache``                  → "Cache"
        ``[]Partition``             → "Partition"
        ``map[string]Config``       → "Config"   (value type; string filtered)
        ``chan Event``              → "Event"
        ``dynacache.Cache``         → "Cache"     (qualifier dropped)
        ``List[Inner]``             → "List"      (generic head)
        ``string`` / ``int``        → None        (builtin)
        ``(Foo, error)``            → None        (parameter_list; the inner
                                                   declarations are captured
                                                   separately)
        ``interface{...}`` / ``struct{...}`` / ``func(...)`` → None (anonymous)

    The qualifier in ``pkg.Cache`` is intentionally dropped: the Go type-ref
    strategy resolves the bare name against same-package siblings and
    imported package files, mirroring the Rust strategy. Keeping the head
    name unqualified matches the symbol-index keys.
    """
    node: Node | None = type_node
    text = ""
    for _ in range(8):
        if node is None:
            return None
        kind = node.type
        if kind == "type_identifier":
            text = _node_text(node, src)
            break
        if kind == "qualified_type":
            name = node.child_by_field_name("name") or next(
                (c for c in reversed(node.children) if c.type == "type_identifier"),
                None,
            )
            text = _node_text(name, src) if name else ""
            break
        if kind == "generic_type":
            node = node.child_by_field_name("type")
            continue
        if kind in ("slice_type", "array_type"):
            node = node.child_by_field_name("element")
            continue
        if kind == "map_type":
            node = node.child_by_field_name("value")
            continue
        if kind == "channel_type":
            node = node.child_by_field_name("value")
            continue
        if kind in ("pointer_type", "parenthesized_type"):
            # No field name on the inner type — take the first named child.
            node = node.named_children[0] if node.named_children else None
            continue
        # parameter_list (multi-return), interface_type, struct_type,
        # function_type, and anything else: no single named type to resolve.
        return None
    else:
        return None

    if not text or (not text[0].isalpha() and text[0] != "_"):
        return None
    if text in _GO_BUILTIN_TYPES:
        return None
    # Single-uppercase-letter heads are overwhelmingly generic type params.
    if len(text) == 1 and text.isupper():
        return None
    return text


# ---------------------------------------------------------------------------
# C / C++ type-reference head extraction
# ---------------------------------------------------------------------------

# Predeclared / standard-library scalar types that never resolve to a
# user-defined struct, so they're dropped before the resolver lookup.
# ``primitive_type`` / ``sized_type_specifier`` nodes are filtered
# structurally below; this set catches the ``<stdint.h>`` / ``<stddef.h>``
# typedefs that the grammar surfaces as plain ``type_identifier`` nodes.
_C_BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        "void", "char", "short", "int", "long", "float", "double",
        "signed", "unsigned", "bool", "_Bool", "_Complex",
        "size_t", "ssize_t", "rsize_t", "ptrdiff_t", "intptr_t", "uintptr_t",
        "int8_t", "int16_t", "int32_t", "int64_t",
        "uint8_t", "uint16_t", "uint32_t", "uint64_t",
        "intmax_t", "uintmax_t", "wchar_t", "wint_t", "char16_t", "char32_t",
        "va_list", "FILE",
    }
)


def _c_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head type name of a C / C++ type expression, or None.

    In C the pointer / array shells wrap the *declarator*, not the type,
    so the captured ``type:`` field is the bare type node:

        ``JSON_Value``                  → "JSON_Value"
        ``struct JSON_Object``          → "JSON_Object"  (named struct ref)
        ``int`` / ``unsigned long``     → None           (primitive)
        ``size_t``                      → None           (stdlib typedef)
        ``Acme::Widget`` (C++)          → "Widget"       (rightmost name)
        ``std::vector<T>`` (C++)        → "vector"       (template head)
        anonymous ``struct {...}``      → None
    """
    node: Node | None = type_node
    text = ""
    for _ in range(6):
        if node is None:
            return None
        kind = node.type
        if kind == "type_identifier":
            text = _node_text(node, src)
            break
        if kind in ("primitive_type", "sized_type_specifier"):
            return None
        if kind in ("struct_specifier", "union_specifier", "enum_specifier", "class_specifier"):
            name = node.child_by_field_name("name")
            if name is None:
                return None  # anonymous aggregate — no named type to resolve
            text = _node_text(name, src)
            break
        if kind == "template_type":
            node = node.child_by_field_name("name")
            continue
        if kind == "qualified_identifier":
            # ``NS::Type`` — take the rightmost name component.
            name = node.child_by_field_name("name")
            node = name if name is not None else (
                node.named_children[-1] if node.named_children else None
            )
            continue
        # type_qualifier (const/volatile) wrappers and anything else:
        # descend into the first named child.
        node = node.named_children[0] if node.named_children else None
    else:
        return None

    if not text or (not text[0].isalpha() and text[0] != "_"):
        return None
    if text in _C_BUILTIN_TYPES:
        return None
    if len(text) == 1 and text.isupper():
        return None
    return text


# Per-language head-identifier extractor for ``@param.type`` captures.
# Defaults to the C#-shaped extractor; languages with a differently-shaped
# type grammar register their own here.
TYPE_HEAD_EXTRACTORS: dict[str, "Callable[[Node, str], str | None]"] = {
    "go": _go_head_type_identifier,
    "c": _c_head_type_identifier,
    "cpp": _c_head_type_identifier,
}


# ---------------------------------------------------------------------------
# Call extraction helpers
# ---------------------------------------------------------------------------


def _count_arguments(arg_node: Node) -> int:
    """Count the number of arguments in an argument/argument_list node."""
    skip_types = frozenset({"(", ")", ",", "[", "]"})
    return sum(1 for child in arg_node.children if child.type not in skip_types)


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
