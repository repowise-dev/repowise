"""Objective-C naming, container, message-send and dedupe helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tree_sitter import Node

from ..extractors import node_text

if TYPE_CHECKING:
    from ..models import Symbol

_OBJC_METHOD_NODE_TYPES = frozenset({"method_declaration", "method_definition"})
_OBJC_CLASS_NODE_TYPES = frozenset({"class_interface", "class_implementation"})

# A declaration and the definition that answers it, keyed the way the dedup
# below pairs them.
_OBJC_DEFINITION_NODE_TYPES = frozenset({"class_implementation", "method_definition"})
_OBJC_DECLARATION_NODE_TYPES = frozenset({"class_interface", "method_declaration"})

# Macros that expand to nothing but are spelled as a bare identifier, so the
# grammar makes them a keyword child of the method they trail.
_OBJC_TRAILING_MACRO_NAMES = frozenset({
    "NS_DESIGNATED_INITIALIZER", "NS_UNAVAILABLE", "NS_REQUIRES_SUPER",
    "NS_RETURNS_INNER_POINTER", "NS_REFINED_FOR_SWIFT", "NS_SWIFT_UI_ACTOR",
    "NS_REQUIRES_NIL_TERMINATION", "UNAVAILABLE_ATTRIBUTE", "DEPRECATED_ATTRIBUTE",
})

# ``typedef NS_ENUM(NSInteger, Kind) { KindA }`` shapes. See _objc_is_macro_enum.
_OBJC_ENUM_MACRO_NAMES = frozenset({
    "NS_ENUM", "NS_OPTIONS", "NS_CLOSED_ENUM", "NS_ERROR_ENUM",
    "CF_ENUM", "CF_OPTIONS", "CF_CLOSED_ENUM",
})


def _objc_selector_name(def_node: Node, default_name: str, src: str) -> str:
    """Join a method's keyword parts into its full selector.

    A keyword is an ``identifier`` child that a ``method_parameter`` follows;
    anything trailing the last parameter is an attribute macro, not part of
    the name. See the Objective-C section of the architecture doc.
    """
    if def_node.type not in _OBJC_METHOD_NODE_TYPES:
        return default_name
    parts = _objc_selector_keywords(def_node, src)
    if parts:
        return "".join(f"{p}:" for p in parts)
    # No parameters at all: the selector is the first keyword, unadorned.
    return _objc_unary_selector(def_node, src) or default_name


def _objc_selector_keywords(def_node: Node, src: str) -> list[str]:
    parts: list[str] = []
    pending: str | None = None
    for child in def_node.children:
        if child.type == "identifier":
            # Only the identifier immediately before a method_parameter is a
            # keyword; a bare trailing one is a macro like
            # NS_DESIGNATED_INITIALIZER, which would otherwise join into the
            # selector and stop the header pairing with its implementation.
            pending = node_text(child, src).strip() or None
        elif child.type == "method_parameter":
            if pending is not None:
                parts.append(pending)
                pending = None
        elif child.type in ("compound_statement", ";"):
            break
    return parts


def _objc_unary_selector(def_node: Node, src: str) -> str | None:
    first = next((c for c in def_node.children if c.type == "identifier"), None)
    if first is None:
        return None
    text = node_text(first, src).strip()
    if not text or text in _OBJC_TRAILING_MACRO_NAMES:
        return None
    return text


def _objc_is_macro_enum(def_node: Node, src: str) -> bool:
    """True for a ``typedef NS_ENUM(Type, Name) { … }`` typedef.

    This grammar has no rule for the macro, so the name argument and the brace
    body land in ERROR nodes and only the *enumerators* survive as
    ``declarator`` children. Capturing those mints a symbol per enum case named
    as though it were the type, and never one for the type itself, so the whole
    typedef is skipped: no symbol beats a wrong one.
    """
    if def_node.type != "type_definition":
        return False
    for child in def_node.children:
        if child.type != "macro_type_specifier":
            continue
        name = child.child_by_field_name("name")
        if name is not None and node_text(name, src).strip() in _OBJC_ENUM_MACRO_NAMES:
            return True
    return False


def _objc_category_name(def_node: Node, default_name: str, src: str) -> str:
    """Name a category for the class it extends plus its own name.

    Two categories on one class are two declarations about it, so naming both
    for the class alone would merge them. A class extension (``Foo ()``) binds
    no ``category`` field and keeps the plain name.
    """
    if def_node.type not in _OBJC_CLASS_NODE_TYPES:
        return default_name
    category = def_node.child_by_field_name("category")
    if category is None:
        return default_name
    text = node_text(category, src).strip()
    return f"{default_name}({text})" if text else default_name


def _objc_symbol_name(def_node: Node, default_name: str, src: str) -> str:
    """The name a captured Objective-C definition carries."""
    if def_node.type in _OBJC_METHOD_NODE_TYPES:
        return _objc_selector_name(def_node, default_name, src)
    return _objc_category_name(def_node, default_name, src)


def _objc_container_node(def_node: Node, container_types: frozenset[str]) -> Node | None:
    """The ``@interface`` / ``@implementation`` / ``@protocol`` around a member."""
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type in container_types:
            return ancestor
        ancestor = ancestor.parent
    return None


def _objc_container_parent(def_node: Node, container_types: frozenset[str], src: str) -> str | None:
    """The name of the container a method or property is declared in.

    The generic nesting walk finds the same ancestor and then reads a ``name``
    field these nodes do not have: each carries its name as a bare first
    ``identifier`` child. Members of a category take the category's own name.
    """
    container = _objc_container_node(def_node, container_types)
    if container is None:
        return None
    identifier = next((c for c in container.children if c.type == "identifier"), None)
    if identifier is None:
        return None
    name = node_text(identifier, src).strip()
    return _objc_category_name(container, name, src) if name else None


def _objc_message_selector(site_node: Node, target_node: Node, src: str) -> str | None:
    """Join a message send's keyword parts, or None for a repeat match.

    ``[view setTitle:t forState:s]`` binds one ``method:`` child per keyword,
    so the one query pattern matches once per keyword. Returns the joined
    selector for the match carrying the first keyword and None for every later
    one, so the extra matches are dropped rather than emitted as targets that
    name nothing. The node's own ``:`` tokens say whether the selector takes
    arguments: ``[obj run]`` is ``run`` and ``[obj run:x]`` is ``run:``.
    """
    methods = site_node.children_by_field_name("method")
    if not methods or methods[0].id != target_node.id:
        return None
    parts = [node_text(m, src).strip() for m in methods]
    parts = [p for p in parts if p]
    if not parts:
        return None
    takes_arguments = any(c.type == ":" for c in site_node.children)
    if not takes_arguments:
        return parts[0]
    return "".join(f"{p}:" for p in parts)


def _objc_call_is_block_variable(site_node: Node, target_name: str, src: str) -> bool:
    """True when a bare-identifier call invokes a block held in a variable.

    Objective-C invokes a block with C call syntax, so ``completionBlock(hit)``
    is a ``call_expression`` on a bare identifier that nothing in the grammar
    separates from a call to a C function. Names like this are ubiquitous as
    both a parameter and a ``@property``, so without the scope check the
    resolver binds the invocation to an unrelated class's property.
    """
    node: Node | None = site_node
    while node is not None:
        if node.type == "compound_statement":
            if _objc_block_declares(node, target_name, src):
                return True
        elif node.type == "method_definition":
            return any(
                child.type == "method_parameter"
                and _objc_declares_name(child, target_name, src)
                for child in node.children
            )
        elif node.type == "function_definition":
            declarator = node.child_by_field_name("declarator")
            return declarator is not None and _objc_declares_name(declarator, target_name, src)
        node = node.parent
    return False


def _objc_block_declares(block: Node, name: str, src: str) -> bool:
    """True when a statement directly in *block* declares *name*."""
    return any(
        statement.type == "declaration" and _objc_declares_name(statement, name, src)
        for statement in block.named_children
    )


def _objc_declares_name(node: Node, name: str, src: str) -> bool:
    """True when *node* binds *name* as a parameter or a variable.

    A declarator nests (``void (^block)(int)``, ``SDBlock done = ^{}``), so
    every identifier under the node is checked rather than only the direct
    children. The node is a parameter or a declaration, never a body, so the
    walk stays small; a nested block body is skipped for the same reason.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in ("identifier", "field_identifier"):
            if node_text(current, src).strip() == name:
                return True
        elif current.type != "compound_statement":
            stack.extend(current.children)
    return False


def _objc_container_family(container_kind: str | None) -> str | None:
    """Which namespace a member belongs to: a protocol's, or a class's.

    An ``@interface`` and its ``@implementation`` are the same class, so a
    declaration in one must still dedupe against a definition in the other; a
    ``@protocol`` of the same name is a different thing entirely.
    """
    return "protocol" if container_kind == "protocol_declaration" else container_kind and "class"


def _dedupe_objc_interface_symbols(
    symbols: list[Symbol], node_types: list[str], container_kinds: list[str | None]
) -> list[Symbol]:
    """Collapse a declaration into the definition standing beside it.

    A ``.m`` routinely opens with a class extension (``@interface Foo ()``)
    declaring the private methods its ``@implementation Foo`` then defines, so
    each builds the same symbol id twice in one file. The definition wins: it
    carries the body. Across two files the split is real and both symbols are
    kept, marked with ``is_declaration`` the way a C header's prototype and its
    ``.c`` definition already are.

    The key carries the container kind because a protocol and a class may share
    a name in one file, and ``-ping`` on the protocol is then a different method
    from ``-ping`` on the class. Keyed on the name and not the signature:
    Objective-C has no overloading, and a declaration and its definition
    routinely differ in parameter names and nullability annotations.
    """
    definition_keys = {
        (_objc_container_family(kind), s.parent_name, s.name)
        for s, nt, kind in zip(symbols, node_types, container_kinds, strict=True)
        if nt in _OBJC_DEFINITION_NODE_TYPES
    }
    seen_declarations: set[tuple[str | None, str | None, str]] = set()
    kept: list[Symbol] = []
    for symbol, node_type, kind in zip(symbols, node_types, container_kinds, strict=True):
        if node_type in _OBJC_DECLARATION_NODE_TYPES:
            key = (_objc_container_family(kind), symbol.parent_name, symbol.name)
            if key in definition_keys or key in seen_declarations:
                continue
            seen_declarations.add(key)
        kept.append(symbol)
    return kept
