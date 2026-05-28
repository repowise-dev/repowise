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
    # TypeScript / JavaScript type positions. Walk-up matches the nearest
    # enclosing declaration; the parameter / field / heritage nodes sit
    # closer than ``class_declaration`` so this dispatch is unambiguous.
    "required_parameter": "param_type",
    "optional_parameter": "param_type",
    "property_signature": "field_type",
    "public_field_definition": "field_type",
    "function_declaration": "return_type",
    "method_definition": "return_type",
    "method_signature": "return_type",
    "arrow_function": "return_type",
    "function_signature": "return_type",
    "type_alias_declaration": "type_alias",
    "type_parameter": "generic_constraint",
    "extends_clause": "extends",
    "extends_type_clause": "extends",
    "implements_clause": "implements",
    # JVM (Java + Kotlin) type positions.
    "formal_parameter": "param_type",
    "object_creation_expression": "composite_literal",
    "local_variable_declaration": "field_type",
    "superclass": "extends",
    "super_interfaces": "implements",
    "type_list": "implements",
    "parameter": "param_type",  # Kotlin function parameter
    "class_parameter": "ctor_param",  # Kotlin primary-ctor parameter
    "variable_declaration": "field_type",  # Kotlin property declaration
    "delegation_specifier": "extends",  # Kotlin class : Bar()
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


# ---------------------------------------------------------------------------
# TypeScript / JavaScript type-reference head extraction
# ---------------------------------------------------------------------------

# Predeclared / lib.dom / lib.es type names that never resolve to a user-
# defined symbol in the workspace. Filtering them before the resolver
# lookup avoids polluting the graph with edges for ubiquitous globals
# (``string``, ``Promise``, ``Pick``) the dead-code analyzer does not
# care about. The list intentionally errs on the side of inclusion: a
# user type colliding with one of these names will fail to resolve via
# the type-ref path, but cross-file usage still surfaces through the
# value-import + call path.
_TS_BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        # Primitives + structural
        "string", "number", "boolean", "bigint", "symbol",
        "void", "null", "undefined", "never", "unknown", "any",
        "object", "this", "Object",
        # Built-in containers / wrappers. ``Map`` / ``Set`` / ``WeakMap``
        # / ``WeakSet`` are intentionally **not** listed: they're routinely
        # shadowed by user-defined types (Hono ``interface Set<E>`` /
        # ``interface Get<E>`` is the canonical case) and filtering them
        # at extraction time hides the same-file rescue.
        "Array", "ReadonlyArray", "Promise", "Awaited", "WeakRef",
        "Date", "RegExp", "Error", "TypeError", "RangeError",
        "SyntaxError", "ReferenceError", "EvalError",
        "Function", "CallableFunction", "NewableFunction",
        "ArrayBuffer", "SharedArrayBuffer", "DataView",
        "Int8Array", "Uint8Array", "Uint8ClampedArray",
        "Int16Array", "Uint16Array", "Int32Array", "Uint32Array",
        "Float32Array", "Float64Array", "BigInt64Array", "BigUint64Array",
        "Iterable", "AsyncIterable", "Iterator", "AsyncIterator",
        "IterableIterator", "AsyncIterableIterator",
        "Generator", "AsyncGenerator", "GeneratorFunction",
        "Proxy", "Reflect", "JSON", "Math",
        # Utility types
        "Record", "Partial", "Required", "Readonly",
        "Pick", "Omit", "Exclude", "Extract", "NonNullable",
        "Parameters", "ConstructorParameters", "ReturnType",
        "InstanceType", "ThisType", "ThisParameterType", "OmitThisParameter",
        "Uppercase", "Lowercase", "Capitalize", "Uncapitalize",
        # Common DOM / Node globals that show up everywhere as parameter
        # types — listing here is a perf optimisation, not correctness.
        "URL", "URLSearchParams", "Request", "Response", "Headers",
        "Blob", "File", "FormData", "FileReader",
        "AbortController", "AbortSignal", "AbortError",
        "EventTarget", "Event", "CustomEvent", "MessageEvent",
        "Element", "HTMLElement", "Node", "Document", "Window",
        "Buffer", "NodeJS",
    }
)


def _ts_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a TypeScript/JavaScript type, or None.

    Unwraps the modifier shells TS layers around a named type so the
    resolver sees the bare identifier:

        ``Foo``                 → "Foo"
        ``Foo[]``               → "Foo"      (array_type)
        ``Promise<Foo>``        → "Promise"  → filtered (builtin)
        ``ns.Foo``              → "Foo"      (nested_type_identifier)
        ``Foo | null``          → None       (union — ambiguous head)
        ``(x: A) => B``         → None       (function_type — A/B are
                                              captured separately as their
                                              own param/return positions)
        ``{ x: number }``       → None       (anonymous object type)
        ``string`` / ``number`` → None       (predefined / builtin)
        ``T``                   → None       (single-uppercase generic)

    Union / intersection / function / object types return None because
    the head isn't a single name; the underlying parameter / field
    captures for each leaf already produced their own ``@param.type``
    captures so the bare leaves are still resolved.
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
        if kind == "identifier":
            # ``extends_clause`` of a class uses ``identifier`` (E in
            # ``class D extends E``); treat it as a type name.
            text = _node_text(node, src)
            break
        if kind == "predefined_type":
            return None
        if kind in ("union_type", "intersection_type", "function_type",
                    "constructor_type", "object_type", "literal_type",
                    "tuple_type", "conditional_type", "mapped_type",
                    "index_type_query", "type_query", "lookup_type",
                    "template_literal_type", "infer_type", "readonly_type"):
            return None
        if kind == "generic_type":
            # ``Foo<T>`` — descend to the bare name; generic args are
            # captured separately if they hold user types.
            inner = node.child_by_field_name("name") or next(
                (c for c in node.named_children if c.type != "type_arguments"),
                None,
            )
            node = inner
            continue
        if kind == "nested_type_identifier":
            # ``ns.Foo`` — rightmost name component is the type itself.
            name = node.child_by_field_name("name") or next(
                (c for c in reversed(node.named_children)
                 if c.type == "type_identifier"),
                None,
            )
            node = name
            continue
        if kind == "array_type":
            # ``T[]`` — element is the first named child.
            node = next(iter(node.named_children), None)
            continue
        if kind == "parenthesized_type":
            node = next(iter(node.named_children), None)
            continue
        if kind == "type_annotation":
            # Shouldn't be reached given the query strips the annotation,
            # but defensive: descend past the colon to the type itself.
            node = next(iter(node.named_children), None)
            continue
        if kind == "constraint":
            # ``extends Cons`` inside a type_parameter — the constraint
            # node wraps the actual constraint type.
            node = next(iter(node.named_children), None)
            continue
        # Anything else (type_predicate, asserts, type_assertion, ...)
        # — descend into the first named child and re-classify.
        node = next(iter(node.named_children), None)
    else:
        return None

    # JS/TS identifiers may start with `$` or `_` in addition to a
    # letter — Zod's ``$ZSF`` interface family is the canonical case.
    if not text or (not text[0].isalpha() and text[0] not in ("_", "$")):
        return None
    if text in _TS_BUILTIN_TYPES:
        return None
    # Single-uppercase-letter heads are overwhelmingly generic type params
    # (T, K, V, U). Skipping them avoids spurious lookups.
    if len(text) == 1 and text.isupper():
        return None
    return text


# ---------------------------------------------------------------------------
# Java type-reference head extraction
# ---------------------------------------------------------------------------

# Primitives + ubiquitous JDK types that never resolve to a user-defined
# Java/Kotlin class in the workspace. Stripping them at extraction time
# avoids polluting the resolver with hopeless lookups. The list errs on
# the side of inclusion: a user class colliding with one of these names
# still surfaces through the value-import path, just not through the
# type-ref path.
_JAVA_BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        # Java primitives + builtin type nodes
        "boolean", "byte", "short", "int", "long", "float", "double",
        "char", "void", "var",
        # java.lang (auto-imported)
        "Object", "String", "Class", "Enum", "Record",
        "Integer", "Long", "Double", "Float", "Boolean", "Character",
        "Byte", "Short", "Number", "Void",
        "Thread", "Runnable", "Runtime", "Process", "ProcessBuilder",
        "Throwable", "Exception", "RuntimeException", "Error",
        "IllegalArgumentException", "IllegalStateException",
        "NullPointerException", "UnsupportedOperationException",
        "IndexOutOfBoundsException", "ClassCastException",
        "ArithmeticException", "SecurityException", "ClassNotFoundException",
        "InterruptedException", "CloneNotSupportedException",
        "StringBuilder", "StringBuffer",
        "Comparable", "Iterable", "AutoCloseable", "Cloneable",
        "Override", "Deprecated", "SuppressWarnings",
        "FunctionalInterface", "SafeVarargs",
        "Math", "System",
        # java.util ubiquitous containers (almost always external when used
        # as a type position; the actual element type is captured separately
        # by the same query via the type_arguments inner capture).
        "List", "ArrayList", "LinkedList",
        "Map", "HashMap", "LinkedHashMap", "TreeMap", "ConcurrentHashMap",
        "Set", "HashSet", "LinkedHashSet", "TreeSet",
        "Collection", "Collections", "Iterator", "Optional",
        "Queue", "Deque", "ArrayDeque", "Stack",
        # java.util.function
        "Function", "BiFunction", "Consumer", "BiConsumer", "Supplier",
        "Predicate", "BiPredicate", "UnaryOperator", "BinaryOperator",
        # java.util.concurrent ubiquitous
        "Future", "CompletableFuture", "Executor", "ExecutorService",
        "CountDownLatch", "Semaphore", "AtomicBoolean", "AtomicInteger",
        "AtomicLong", "AtomicReference",
        # java.time
        "Instant", "Duration", "LocalDate", "LocalTime", "LocalDateTime",
        "ZonedDateTime", "OffsetDateTime", "Period", "ZoneId",
        # java.io
        "File", "InputStream", "OutputStream", "Reader", "Writer",
        "IOException", "Serializable",
    }
)


def _java_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head type identifier of a Java type expression, or None.

    Examples:
        ``Bar``                         → "Bar"
        ``java.util.List<Foo>``         → "List"   → filtered (builtin)
        ``com.x.y.Z``                   → "Z"
        ``Foo.Bar``                     → "Bar"   (inner type)
        ``Foo[]``                       → "Foo"
        ``int`` / ``void`` / ``long``   → None
        ``T``                           → None    (generic parameter)
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
        if kind in (
            "void_type", "integral_type", "floating_point_type",
            "boolean_type",
        ):
            return None
        if kind == "scoped_type_identifier":
            # ``com.x.y.Z`` / ``Foo.Bar`` — take the rightmost type_identifier
            inner_ids = [c for c in node.children if c.type == "type_identifier"]
            if not inner_ids:
                return None
            text = _node_text(inner_ids[-1], src)
            break
        if kind == "generic_type":
            # ``Foo<T>`` — descend to the bare name; generic args are
            # captured separately by their own type_arguments inner captures.
            inner = next(
                (c for c in node.named_children
                 if c.type in ("type_identifier", "scoped_type_identifier")),
                None,
            )
            node = inner
            continue
        if kind == "array_type":
            # ``Foo[]`` — element child has no field name; take first named.
            inner = next(iter(node.named_children), None)
            node = inner
            continue
        if kind == "annotated_type":
            # ``@NonNull Foo`` — last named child is the type.
            node = next(
                (c for c in reversed(node.named_children)
                 if c.type not in ("annotation", "marker_annotation")),
                None,
            )
            continue
        # Anything else (wildcard, type_parameter, ...) — descend.
        node = next(iter(node.named_children), None)
    else:
        return None

    if not text or (not text[0].isalpha() and text[0] != "_"):
        return None
    if text in _JAVA_BUILTIN_TYPES:
        return None
    # Single-uppercase-letter heads are overwhelmingly generic type params.
    if len(text) == 1 and text.isupper():
        return None
    return text


# ---------------------------------------------------------------------------
# Kotlin type-reference head extraction
# ---------------------------------------------------------------------------

_KOTLIN_BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        # Kotlin primitives (kotlin package, auto-imported)
        "Boolean", "Byte", "Short", "Int", "Long", "Float", "Double", "Char",
        "String", "Unit", "Nothing", "Any", "Number",
        "Array", "IntArray", "LongArray", "ByteArray", "ShortArray",
        "FloatArray", "DoubleArray", "CharArray", "BooleanArray",
        "List", "MutableList", "ArrayList",
        "Map", "MutableMap", "HashMap", "LinkedHashMap",
        "Set", "MutableSet", "HashSet", "LinkedHashSet",
        "Collection", "MutableCollection",
        "Iterable", "MutableIterable", "Iterator", "MutableIterator",
        "Sequence", "Pair", "Triple", "Result",
        "Comparable", "Comparator",
        "Throwable", "Exception", "RuntimeException", "Error",
        "IllegalArgumentException", "IllegalStateException",
        "NullPointerException", "UnsupportedOperationException",
        "IndexOutOfBoundsException", "ClassCastException",
        "Lazy", "Regex", "Range", "IntRange", "LongRange", "CharRange",
        "Enum", "Annotation",
        # kotlin.io / kotlin.text / kotlin.collections ubiquitous
        "Reader", "Writer", "BufferedReader", "BufferedWriter",
        # Coroutines + JVM common
        "Object", "Function", "Runnable", "Class", "Void",
    }
)


def _kotlin_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a Kotlin type expression, or None.

    Examples:
        ``Bar``                         → "Bar"
        ``Foo?``                        → "Foo"   (nullable_type unwrapped)
        ``List<Foo>``                   → "List"  → filtered (builtin)
        ``com.x.Foo``                   → "Foo"   (dotted user_type)
        ``Foo.Bar``                     → "Bar"
        ``() -> Foo``                   → None    (function_type — skipped)
        ``Unit`` / ``Any`` / ``String`` → None    (builtin)
    """
    node: Node | None = type_node
    text = ""
    for _ in range(8):
        if node is None:
            return None
        kind = node.type
        if kind == "user_type":
            # ``Foo`` / ``Foo<...>`` / ``ns.Foo``.
            # Children: identifier, type_arguments, possibly more dotted parts.
            # Rightmost identifier is the head; type_arguments contains the
            # generic args (captured separately by their own type-ref).
            inner_ids = [c for c in node.children if c.type == "identifier"]
            if not inner_ids:
                return None
            text = _node_text(inner_ids[-1], src)
            break
        if kind == "identifier":
            text = _node_text(node, src)
            break
        if kind == "nullable_type":
            # Unwrap ``Foo?`` to the underlying user_type.
            inner = next(iter(node.named_children), None)
            node = inner
            continue
        if kind in ("function_type", "parenthesized_type"):
            # () -> Foo, (() -> Foo) — leaf type names are not captured.
            return None
        if kind == "type_reference":
            node = next(iter(node.named_children), None)
            continue
        if kind == "type_projection":
            # `<out Foo>` / `<in Foo>` / `<Foo>` — last named child is the type.
            node = next(iter(node.named_children), None)
            continue
        # Anything else — descend.
        node = next(iter(node.named_children), None)
    else:
        return None

    if not text or (not text[0].isalpha() and text[0] != "_"):
        return None
    if text in _KOTLIN_BUILTIN_TYPES:
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
    "typescript": _ts_head_type_identifier,
    "javascript": _ts_head_type_identifier,
    "java": _java_head_type_identifier,
    "kotlin": _kotlin_head_type_identifier,
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
