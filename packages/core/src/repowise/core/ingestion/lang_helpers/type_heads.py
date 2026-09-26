"""Per-language head-identifier extractors for ``@param.type`` captures."""

from __future__ import annotations

from collections.abc import Callable

from tree_sitter import Node

from ..extractors import fsharp_type_name, node_text
from ..language_data import get_builtin_types
from ..type_names import is_resolvable_type_name

_PARAM_ORIGIN_BY_ANCESTOR: dict[str, str] = {
    "type_argument_list": "generic_argument",
    "typeof_expression": "typeof",
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
    # JVM (Java + Kotlin) type positions. Note: ``parameter`` is NOT
    # mapped here because tree-sitter-c-sharp also uses ``parameter``
    # for ctor params and adding it would override C#'s walk to the
    # enclosing ``constructor_declaration``. Kotlin function-parameter
    # origin therefore falls through to ``function_declaration`` →
    # "return_type" (imprecise but harmless — origin is provenance only).
    "formal_parameter": "param_type",
    "object_creation_expression": "composite_literal",
    "local_variable_declaration": "field_type",
    "superclass": "extends",
    "super_interfaces": "implements",
    "type_list": "implements",
    "class_parameter": "ctor_param",  # Kotlin primary-ctor parameter
    "variable_declaration": "field_type",  # Kotlin property declaration
    "delegation_specifier": "extends",  # Kotlin class : Bar()
    # Pascal type positions — node types are Pascal-only, no collision risk.
    "declField": "field_type",
    "declArg": "param_type",
    "declVar": "local_var_type",
    "declProc": "return_type",
    "exprArgs": "framework_ctor",  # Application.CreateForm(TClass, Var)
}


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
# Shared walk
# ---------------------------------------------------------------------------

# One step of a head walk: the head's text when this node names the type, the
# node to descend into when it wraps one, or None when there is no single head.
HeadStep = Callable[[Node, str], str | Node | None]


def _walk_to_head(type_node: Node, src: str, step: HeadStep, max_depth: int) -> str | None:
    """Follow *step* down from *type_node* until it yields the head's text."""
    node: Node | None = type_node
    for _ in range(max_depth):
        if node is None:
            return None
        outcome = step(node, src)
        if isinstance(outcome, str):
            return outcome
        node = outcome
    return None


def _resolvable(text: str | None, language: str) -> str | None:
    return text if text and is_resolvable_type_name(text, language) else None


def _first_named_child(node: Node) -> Node | None:
    return node.named_children[0] if node.named_children else None


def _first_descendant(node: Node, type_name: str) -> Node | None:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == type_name:
            return current
        stack.extend(current.children)
    return None


# ---------------------------------------------------------------------------
# C# (the default extractor)
# ---------------------------------------------------------------------------

_CSHARP_MODIFIER_TYPES = ("nullable_type", "ref_type", "pointer_type", "array_type")
_CSHARP_MODIFIER_TRIVIA = (",", "?", "*", "&", "ref", "out", "in", "[", "]")


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
    name. Generic-arg recursion is intentionally NOT done here — each generic
    argument is captured from its own ``type_argument_list`` node, including
    nested arguments and invocation-only type uses.
    """
    head_node = _csharp_unwrap_modifiers(type_node)
    if head_node is None or head_node.type == "tuple_type":
        # Tuple elements aren't single types.
        return None
    return _resolvable(_csharp_head_text(head_node, src), "csharp")


def _csharp_unwrap_modifiers(type_node: Node) -> Node | None:
    """Strip nullable / ref / pointer / array shells down to the named type.

    tree-sitter-c-sharp puts the inner type at field ``type`` or as the first
    non-trivia child.
    """
    head_node: Node | None = type_node
    for _ in range(6):
        if head_node is None or head_node.type not in _CSHARP_MODIFIER_TYPES:
            return head_node
        inner = head_node.child_by_field_name("type")
        if inner is None:
            inner = next(
                (c for c in head_node.children if c.type not in _CSHARP_MODIFIER_TRIVIA),
                None,
            )
        head_node = inner
    return head_node


def _csharp_head_text(head_node: Node, src: str) -> str:
    kind = head_node.type
    if kind in ("identifier", "predefined_type"):
        return node_text(head_node, src)
    if kind == "generic_name":
        name_child = head_node.child_by_field_name("name") or next(
            (c for c in head_node.children if c.type == "identifier"),
            None,
        )
        return node_text(name_child, src) if name_child else ""
    if kind == "qualified_name":
        # `Foo.Bar.Baz` — take the rightmost identifier
        idents = [c for c in head_node.children if c.type == "identifier"]
        return node_text(idents[-1], src) if idents else ""
    # Unknown shape — fall back to first identifier in the subtree
    ident = _first_descendant(head_node, "identifier")
    return node_text(ident, src) if ident else ""


# ---------------------------------------------------------------------------
# Go type-reference head extraction
# ---------------------------------------------------------------------------

# Go shells that name their inner type by field.
_GO_INNER_FIELD = {
    "generic_type": "type",
    "slice_type": "element",
    "array_type": "element",
    "map_type": "value",
    "channel_type": "value",
}


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
    return _resolvable(_walk_to_head(type_node, src, _go_head_step, 8), "go")


def _go_head_step(node: Node, src: str) -> str | Node | None:
    kind = node.type
    if kind == "type_identifier":
        return node_text(node, src)
    if kind == "qualified_type":
        name = node.child_by_field_name("name") or next(
            (c for c in reversed(node.children) if c.type == "type_identifier"),
            None,
        )
        return node_text(name, src) if name else ""
    field = _GO_INNER_FIELD.get(kind)
    if field is not None:
        return node.child_by_field_name(field)
    if kind in ("pointer_type", "parenthesized_type"):
        # No field name on the inner type — take the first named child.
        return _first_named_child(node)
    # parameter_list (multi-return), interface_type, struct_type,
    # function_type, and anything else: no single named type to resolve.
    return None


# ---------------------------------------------------------------------------
# C / C++ type-reference head extraction
# ---------------------------------------------------------------------------

_C_AGGREGATE_SPECIFIERS = ("struct_specifier", "union_specifier", "enum_specifier", "class_specifier")


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
    # cpp shares this extractor; the builtin set is identical for both.
    return _resolvable(_walk_to_head(type_node, src, _c_head_step, 6), "c")


def _c_head_step(node: Node, src: str) -> str | Node | None:
    kind = node.type
    if kind == "type_identifier":
        return node_text(node, src)
    if kind in ("primitive_type", "sized_type_specifier"):
        return None
    if kind in _C_AGGREGATE_SPECIFIERS:
        name = node.child_by_field_name("name")
        # An anonymous aggregate has no named type to resolve.
        return node_text(name, src) if name is not None else None
    if kind == "template_type":
        return node.child_by_field_name("name")
    if kind == "qualified_identifier":
        # ``NS::Type`` — take the rightmost name component.
        name = node.child_by_field_name("name")
        if name is not None:
            return name
        return node.named_children[-1] if node.named_children else None
    # type_qualifier (const/volatile) wrappers and anything else:
    # descend into the first named child.
    return _first_named_child(node)


# ---------------------------------------------------------------------------
# TypeScript / JavaScript type-reference head extraction
# ---------------------------------------------------------------------------

# Shapes whose head is not a single name. The leaves inside them are captured
# by their own ``@param.type`` patterns, so they still resolve.
_TS_NO_SINGLE_HEAD = frozenset({
    "predefined_type",
    "union_type", "intersection_type", "function_type",
    "constructor_type", "object_type", "literal_type",
    "tuple_type", "conditional_type", "mapped_type",
    "index_type_query", "type_query", "lookup_type",
    "template_literal_type", "infer_type", "readonly_type",
})


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
    return _resolvable(_walk_to_head(type_node, src, _ts_head_step, 8), "typescript")


def _ts_head_step(node: Node, src: str) -> str | Node | None:
    kind = node.type
    if kind in ("type_identifier", "identifier"):
        # ``extends_clause`` of a class uses ``identifier`` (E in
        # ``class D extends E``); treat it as a type name.
        return node_text(node, src)
    if kind in _TS_NO_SINGLE_HEAD:
        return None
    if kind == "generic_type":
        # ``Foo<T>`` — descend to the bare name; generic args are
        # captured separately if they hold user types.
        return node.child_by_field_name("name") or next(
            (c for c in node.named_children if c.type != "type_arguments"),
            None,
        )
    if kind == "nested_type_identifier":
        # ``ns.Foo`` — rightmost name component is the type itself.
        return node.child_by_field_name("name") or next(
            (c for c in reversed(node.named_children) if c.type == "type_identifier"),
            None,
        )
    # ``T[]``, parentheses, a stray type_annotation, a ``constraint`` and
    # anything else (type_predicate, asserts, ...) wrap the type as their
    # first named child.
    return _first_named_child(node)


# ---------------------------------------------------------------------------
# Java type-reference head extraction
# ---------------------------------------------------------------------------

_JAVA_PRIMITIVE_TYPES = ("void_type", "integral_type", "floating_point_type", "boolean_type")


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
    return _resolvable(_walk_to_head(type_node, src, _java_head_step, 8), "java")


def _java_head_step(node: Node, src: str) -> str | Node | None:
    kind = node.type
    if kind == "type_identifier":
        return node_text(node, src)
    if kind in _JAVA_PRIMITIVE_TYPES:
        return None
    if kind == "scoped_type_identifier":
        # ``com.x.y.Z`` / ``Foo.Bar`` — take the rightmost type_identifier
        inner_ids = [c for c in node.children if c.type == "type_identifier"]
        return node_text(inner_ids[-1], src) if inner_ids else None
    if kind == "generic_type":
        # ``Foo<T>`` — descend to the bare name; generic args are
        # captured separately by their own type_arguments inner captures.
        return next(
            (c for c in node.named_children
             if c.type in ("type_identifier", "scoped_type_identifier")),
            None,
        )
    if kind == "annotated_type":
        # ``@NonNull Foo`` — last named child is the type.
        return next(
            (c for c in reversed(node.named_children)
             if c.type not in ("annotation", "marker_annotation")),
            None,
        )
    # ``Foo[]`` and anything else (wildcard, type_parameter, ...) — descend.
    return _first_named_child(node)


# ---------------------------------------------------------------------------
# Kotlin type-reference head extraction
# ---------------------------------------------------------------------------

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
    return _resolvable(_walk_to_head(type_node, src, _kotlin_head_step, 8), "kotlin")


def _kotlin_head_step(node: Node, src: str) -> str | Node | None:
    kind = node.type
    if kind == "user_type":
        # ``Foo`` / ``Foo<...>`` / ``ns.Foo``.
        # Children: identifier, type_arguments, possibly more dotted parts.
        # Rightmost identifier is the head; type_arguments contains the
        # generic args (captured separately by their own type-ref).
        inner_ids = [c for c in node.children if c.type == "identifier"]
        return node_text(inner_ids[-1], src) if inner_ids else None
    if kind == "identifier":
        return node_text(node, src)
    if kind in ("function_type", "parenthesized_type"):
        # () -> Foo, (() -> Foo) — leaf type names are not captured.
        return None
    # ``Foo?``, a type_reference, a ``<out Foo>`` projection and anything
    # else wrap the type as their first named child.
    return _first_named_child(node)


# ---------------------------------------------------------------------------
# Rust type-reference head extraction
# ---------------------------------------------------------------------------

# Builtin, or no single head name to resolve.
_RUST_NO_SINGLE_HEAD = (
    "primitive_type", "tuple_type", "unit_type", "array_type",
    "function_type", "never_type", "empty_type",
)


def _rust_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a Rust type expression, or None.

    Rust needs its own extractor because the C#-shaped default spells a type
    name ``identifier`` while tree-sitter-rust spells it ``type_identifier``.
    The default therefore returned None for every bare Rust type, and for
    ``std::io::Error`` returned the leftmost segment ``io`` — a crate name,
    not the type.

    Examples:
        ``MyType``              -> "MyType"
        ``&Other`` / ``&mut T`` -> "Other"  (reference_type unwrapped)
        ``dyn MyTrait``         -> "MyTrait"
        ``impl Shape``          -> "Shape"
        ``Box<Inner>``          -> "Box"    -> filtered (builtin)
        ``std::io::Error``      -> "Error"  (rightmost component)
        ``u32`` / ``String``    -> None     (builtin)
        ``T``                   -> None     (single-letter generic param)

    Generic arguments are not recursed into: ``Vec<Foo>`` yields the head
    ``Vec`` only. Foo reaches the resolver through its own capture, matching
    the Go and Kotlin extractors.
    """
    return _resolvable(_walk_to_head(type_node, src, _rust_head_step, 8), "rust")


def _rust_head_step(node: Node, src: str) -> str | Node | None:
    kind = node.type
    if kind == "type_identifier":
        return node_text(node, src)
    if kind == "scoped_type_identifier":
        # ``std::io::Error`` — the type is the rightmost component.
        return node.child_by_field_name("name")
    if kind in ("reference_type", "pointer_type", "generic_type"):
        # ``&T`` / ``&mut T`` / ``*const T`` put the referent in the type
        # field (``mut`` and ``const`` are unnamed children), and
        # ``Box<Inner>`` its constructor; args are captured separately.
        return node.child_by_field_name("type")
    if kind in ("dynamic_type", "abstract_type"):
        # ``dyn Trait`` / ``impl Trait`` — sole named child is the trait.
        return node.child_by_field_name("trait") or _first_named_child(node)
    if kind in _RUST_NO_SINGLE_HEAD:
        return None
    # Unknown shape - descend into the first named child and re-classify.
    return _first_named_child(node)


def _rust_type_param_name(param: Node, src: str) -> str | None:
    """Return the bound name of a ``type_parameter``/``const_parameter`` node.

    Both node types put the binding name first, before any trait bound or
    default (``T: Clone``, ``D = Foo``, ``const N: usize``) — later
    ``type_identifier`` children are constraints, not the binding itself.
    """
    for child in param.children:
        if child.type in ("type_identifier", "identifier"):
            return node_text(child, src).strip()
    return None


def _rust_binds_type_param(item: Node, target_name: str, src: str) -> bool:
    """True if *item*'s own ``<...>`` list binds *target_name*."""
    return any(
        param.type in ("type_parameter", "const_parameter")
        and _rust_type_param_name(param, src) == target_name
        for child in item.children
        if child.type == "type_parameters"
        for param in child.children
    )


def _rust_shadowed_by_type_param(node: Node, target_name: str, src: str) -> bool:
    """True if *target_name* is bound as a generic type parameter by an
    ancestor item of *node* (``struct Wrapper<Item> { value: Item }``).

    Rust's field/generic-argument type captures can't distinguish a type
    parameter's own name from a real type reference — both are plain
    ``type_identifier`` nodes. Without this check, a type parameter that
    happens to share a name with a real struct/enum produces a false
    "used" edge to that struct, hiding it from dead-code detection even
    when it is genuinely unreferenced.
    """
    ancestor = node.parent
    while ancestor is not None:
        if _rust_binds_type_param(ancestor, target_name, src):
            return True
        ancestor = ancestor.parent
    return False


# ---------------------------------------------------------------------------
# Pascal, F#, Elixir and Objective-C type-reference head extraction
# ---------------------------------------------------------------------------

# The child holding the type name in a generic or unit-qualified ``typeref``.
_PASCAL_HEAD_FIELD = {"typerefTpl": "entity", "typerefDot": "rhs"}


def _pascal_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a Pascal ``typeref`` type expression.

    Examples:
        ``TFoo``                  → "TFoo"
        ``TList<TBar>``           → "TList" (generic head only; the arg
                                     isn't captured separately -- no
                                     ``typerefArgs``-scoped query pattern
                                     exists yet)
        ``Ns.TQualified``         → "TQualified" (rightmost segment;
                                     Pascal's unit-qualification puts the
                                     type name last, same convention as
                                     C#'s ``qualified_name`` handling)
        ``Integer`` / ``TObject`` → None (builtin / VCL root type, never
                                     has an in-repo declaration)

    ``type_node`` is the ``typeref`` node pascal.scm captures directly (a
    ``declProc`` return type) or via the intermediate ``type`` wrapper
    (field/parameter/local-variable positions) -- the wrapper itself is
    never captured, only the ``typeref`` inside it, so this always starts
    from ``typeref`` regardless of which position it came from. A
    ``typeref`` wraps exactly one of: a bare ``identifier``, a generic
    ``typerefTpl`` (``entity`` field holds the head), or a qualified
    ``typerefDot`` (``rhs`` field holds the type name -- ``lhs`` is the
    unit qualifier).
    """
    head: Node | None = type_node
    if head.type == "typeref":
        head = _first_named_child(head)
        if head is None:
            return None
    field = _PASCAL_HEAD_FIELD.get(head.type)
    if field is not None:
        head = head.child_by_field_name(field)
    if head is None or head.type != "identifier":
        return None
    text = node_text(head, src).strip()
    if not text or text in get_builtin_types("pascal"):
        return None
    return text


def _fsharp_head_type_identifier(type_node: Node, src: str) -> str | None:
    """The resolvable head of an F# type annotation.

    ``fsharp_type_name`` does the walk; this adds the filter the type-
    reference pass needs, since a primitive or an FSharp.Core type
    constructor never has an in-repo declaration.
    """
    text = fsharp_type_name(type_node, src)
    if not text or text in get_builtin_types("fsharp"):
        return None
    return text


def _elixir_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the module name in an Elixir type position.

    A captured type is always a whole ``alias`` leaf (``%Foo.Bar{}``,
    ``@behaviour GenServer``), and the grammar keeps a dotted module name in
    one node, so the head is the node's own text. No unwrapping to do, and no
    generic-argument shape to recurse into -- unlike every other language
    here, which is why the shared C#-shaped extractor cannot serve.
    """
    if type_node.type != "alias":
        return None
    return node_text(type_node, src).strip() or None


def _objc_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head class name of an Objective-C type position, or None.

    Examples:
        ``(NSString *)``      -> None       (Foundation, never in-repo)
        ``(AFHTTPClient *)``  -> the name
        ``(instancetype)``    -> None       (builtin)
        ``(void)`` / ``(id)`` -> None       (primitive / builtin)
        ``@class Helper;``    -> "Helper"

    The capture is a ``type_name`` wrapper for a method return or parameter
    type, a bare ``type_identifier`` for a property, and a plain
    ``identifier`` for a ``@class`` forward declaration or a protocol name, so
    all four shapes start here. A pointer star lives in an
    ``abstract_pointer_declarator`` inside the ``type_name``, which is skipped
    rather than unwrapped: the star is not part of the name.
    """
    return _resolvable(_walk_to_head(type_node, src, _objc_head_step, 6), "objectivec")


def _objc_head_step(node: Node, src: str) -> str | Node | None:
    kind = node.type
    if kind in ("type_identifier", "identifier"):
        return node_text(node, src).strip()
    if kind in ("primitive_type", "sized_type_specifier", "typedefed_specifier"):
        return None
    if kind in ("struct_specifier", "union_specifier", "enum_specifier"):
        name = node.child_by_field_name("name")
        # An anonymous aggregate has no named type to resolve.
        return node_text(name, src).strip() if name is not None else None
    return _first_named_child(node)


# Per-language head-identifier extractor for ``@param.type`` captures.
# Defaults to the C#-shaped extractor; languages with a differently-shaped
# type grammar register their own here.
TYPE_HEAD_EXTRACTORS: dict[str, Callable[[Node, str], str | None]] = {
    "go": _go_head_type_identifier,
    "c": _c_head_type_identifier,
    "cpp": _c_head_type_identifier,
    "typescript": _ts_head_type_identifier,
    "javascript": _ts_head_type_identifier,
    "java": _java_head_type_identifier,
    "kotlin": _kotlin_head_type_identifier,
    "rust": _rust_head_type_identifier,
    "pascal": _pascal_head_type_identifier,
    "elixir": _elixir_head_type_identifier,
    "fsharp": _fsharp_head_type_identifier,
    "objectivec": _objc_head_type_identifier,
}
