"""Elixir definition, module-parent and call-filter helpers."""

from __future__ import annotations

from tree_sitter import Node

from ..extractors import node_text

# Elixir keywords that DEFINE rather than call. Every one of them parses as a
# `call` whose target identifier holds the keyword text, so the head it takes
# (`add(a, b)` in `def add(a, b)`) is a `call` node too.
_ELIXIR_DEFINITION_KEYWORDS = frozenset(
    {
        "def",
        "defp",
        "defmacro",
        "defmacrop",
        "defguard",
        "defguardp",
        "defdelegate",
        "defmodule",
        "defprotocol",
        "defimpl",
        "defexception",
        "defstruct",
    }
)

# Module attributes whose body is a type expression, not code. `@spec
# add(integer(), integer()) :: integer()` holds three `call` nodes and calls
# nothing; every other attribute (`@config Application.compile_env(…)`) holds
# real code and keeps its edges.
_ELIXIR_TYPESPEC_ATTRIBUTES = frozenset(
    {"spec", "type", "typep", "opaque", "callback", "macrocallback"}
)

# Elixir keywords that own a nested definition -- the parent a `def` reports.
# `defimpl` is here because its definitions belong to the implementation module
# the compiler generates, not to whatever module the block happens to sit in.
_ELIXIR_MODULE_KEYWORDS = frozenset({"defmodule", "defprotocol", "defimpl"})

# Node kinds that hold a list of statements. Reaching one while walking out of
# an expression means the enclosing statement has been left.
_ELIXIR_STATEMENT_HOLDERS = frozenset({"do_block", "block", "source", "stab_clause"})


def _elixir_call_target_name(call_node: Node, src: str) -> str | None:
    """The keyword or function name in an Elixir ``call`` node's target."""
    if call_node.type != "call":
        return None
    target = call_node.child_by_field_name("target")
    if target is None or target.type != "identifier":
        return None
    return node_text(target, src).strip() or None


def _elixir_call_is_definitional(node: Node, src: str) -> bool:
    """True when an Elixir ``call`` node defines or describes, never invokes.

    Two shapes reach the call queries and must not become graph edges, and
    neither can be excluded by a query predicate, which sees only the node's
    own text and not its parent:

    * a definition head -- ``def add(a, b)`` puts ``add(a, b)`` in the
      ``arguments`` of a call to ``def``, so the head reads as a call to the
      function being defined (a self-edge on every function in the repo). The
      ``when``-guard form wraps that head in a ``binary_operator`` first.
    * a module attribute -- ``@doc "…"`` is a call to ``doc``, and any
      ``@whatever value`` is a call to ``whatever``. Inside a typespec
      attribute the body is types, so its calls are dropped too; inside any
      other attribute the body is ordinary code and keeps its edges.
    """
    return _elixir_is_definition_head(node, src) or _elixir_in_attribute(node, src)


def _elixir_is_definition_head(node: Node, src: str) -> bool:
    """True when *node* (or its ``when`` guard) is the first argument of a ``def``."""
    head = _elixir_guarded_head(node)
    parent = head.parent
    if parent is None or parent.type != "arguments":
        return False
    named = parent.named_children
    if not named or named[0].id != head.id:
        return False
    outer = parent.parent
    return outer is not None and _elixir_call_target_name(outer, src) in _ELIXIR_DEFINITION_KEYWORDS


def _elixir_guarded_head(node: Node) -> Node:
    """The ``when`` expression *node* is the left side of, else *node* itself."""
    parent = node.parent
    if parent is None or parent.type != "binary_operator":
        return node
    operator = parent.child_by_field_name("operator")
    if operator is None or operator.type != "when":
        return node
    left = parent.child_by_field_name("left")
    return parent if left is not None and left.id == node.id else node


def _elixir_in_attribute(node: Node, src: str) -> bool:
    """True when *node* is a module attribute's name or sits in a typespec body."""
    # Walk out to the `@` unary_operator that opens this statement. Bounded by
    # the statement rather than by a hop count: a union return type nests one
    # binary_operator per member, so `@spec f(t) :: a() | b() | c() | d()` sits
    # deeper than any fixed budget and used to leak its type names as calls.
    ancestor: Node | None = node
    while ancestor is not None:
        holder = ancestor.parent
        if holder is None or holder.type in _ELIXIR_STATEMENT_HOLDERS:
            return False
        if _elixir_is_attribute_operator(holder):
            return (
                ancestor.id == node.id
                or _elixir_call_target_name(ancestor, src) in _ELIXIR_TYPESPEC_ATTRIBUTES
            )
        ancestor = holder
    return False


def _elixir_is_attribute_operator(node: Node) -> bool:
    if node.type != "unary_operator":
        return False
    operator = node.child_by_field_name("operator")
    return operator is not None and operator.type == "@"


def _elixir_arguments(call_node: Node) -> Node | None:
    return next((child for child in call_node.named_children if child.type == "arguments"), None)


def _elixir_definition_alias(call_node: Node, src: str) -> str | None:
    """The first ``alias`` argument of a definition call, i.e. its module name."""
    arguments = _elixir_arguments(call_node)
    if arguments is None or not arguments.named_children:
        return None
    first = arguments.named_children[0]
    if first.type != "alias":
        return None
    return node_text(first, src).strip() or None


def _elixir_defimpl_for_type(call_node: Node, src: str) -> str | None:
    """The ``for:`` type of a ``defimpl Proto, for: Type`` block."""
    arguments = _elixir_arguments(call_node)
    if arguments is None:
        return None
    for child in arguments.named_children:
        if child.type != "keywords":
            continue
        value = _elixir_keyword_value(child, "for", src)
        if value is not None:
            return node_text(value, src).strip() or None
    return None


def _elixir_keyword_value(keywords: Node, key_name: str, src: str) -> Node | None:
    """The value node of the first ``key_name:`` pair in a keyword list."""
    for pair in keywords.named_children:
        key = pair.child_by_field_name("key")
        value = pair.child_by_field_name("value")
        if key is None or value is None:
            continue
        if node_text(key, src).strip().rstrip(":") == key_name:
            return value
    return None


def _elixir_enclosing_module_alias(node: Node, src: str) -> str | None:
    """The nearest enclosing ``defmodule``/``defprotocol`` name."""
    ancestor = node.parent
    while ancestor is not None:
        if _elixir_call_target_name(ancestor, src) in ("defmodule", "defprotocol"):
            return _elixir_definition_alias(ancestor, src)
        ancestor = ancestor.parent
    return None


def _elixir_defimpl_name(call_node: Node, src: str) -> str | None:
    """The module name the compiler generates for a ``defimpl`` block.

    ``defimpl Jason.Encoder, for: Tuple`` compiles to a module called
    ``Jason.Encoder.Tuple``, and that name is used here. Without it the
    definitions inside the block are attributed to whatever module encloses it,
    where they read as unused exports of a module that never declared them, and
    two implementations of one protocol in one file mint two symbols under one
    id.

    The ``for:``-less form is only legal inside a ``defmodule`` and implements
    the protocol for that module, so the enclosing module supplies the type.
    """
    protocol = _elixir_definition_alias(call_node, src)
    if protocol is None:
        return None
    target_type = _elixir_defimpl_for_type(call_node, src) or _elixir_enclosing_module_alias(
        call_node, src
    )
    return f"{protocol}.{target_type}" if target_type else protocol


def _elixir_symbol_name(def_node: Node, default_name: str, src: str) -> str:
    """The name a captured Elixir definition carries.

    Only ``defimpl`` differs from its captured text; everything else names
    itself. See ``_elixir_defimpl_name``.
    """
    if _elixir_call_target_name(def_node, src) != "defimpl":
        return default_name
    return _elixir_defimpl_name(def_node, src) or default_name


def _elixir_is_template_definition(def_node: Node, src: str) -> bool:
    """True for a definition inside ``quote do ... end``.

    A ``def`` in a ``quote`` block is the body of a macro: it defines nothing in
    the module that writes it and everything in the module that later invokes
    the macro, which is a module no parser can name. Captured as a symbol it
    reads as a method of the wrong module, and then as an unused export of it.
    """
    ancestor = def_node.parent
    while ancestor is not None:
        if _elixir_call_target_name(ancestor, src) == "quote":
            return True
        ancestor = ancestor.parent
    return False


def _elixir_module_parent(def_node: Node, src: str) -> str | None:
    """Return the enclosing ``defmodule``/``defprotocol`` name, or None.

    The generic nesting walk in ``ASTParser._find_parent`` reads
    ``ancestor.child_by_field_name("name")``, and an Elixir ``call`` has no
    ``name`` field -- the module name is the first argument of a call to
    ``defmodule``. Nesting is the only thing that says which module a
    function belongs to, so without this every ``def`` reads as a free
    function.
    """
    ancestor = def_node.parent
    while ancestor is not None:
        keyword = _elixir_call_target_name(ancestor, src)
        if keyword in _ELIXIR_MODULE_KEYWORDS:
            if keyword == "defimpl":
                return _elixir_defimpl_name(ancestor, src)
            return _elixir_definition_alias(ancestor, src)
        ancestor = ancestor.parent
    return None
