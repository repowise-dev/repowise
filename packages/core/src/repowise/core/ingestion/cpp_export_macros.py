"""C/C++ export-macro recovery: macro-decorated types and forward-declaration helpers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cached_property

from tree_sitter import Node

from .cpp_macro_facts import _build_cpp_macro_facts, _cpp_normalize_identifier, _CppMacroFacts
from .extractors import node_text as _node_text

# Type nodes shared by a definition and its forward declaration; only ``body``
# tells them apart. ``union_specifier`` is absent because no query captures one;
# add it here if one ever does, or ``union U;`` reads as a definition.
_CPP_TYPE_SPECIFIER_NODES = frozenset({"class_specifier", "struct_specifier", "enum_specifier"})
_CPP_EXPORT_FORWARD_DECLARATION_NODES = frozenset({"declaration", "field_declaration"})

# Keywords that open a C/C++ scope. tree-sitter reads an unknown macro line
# right before one (``FMT_BEGIN_NAMESPACE``, ``ABSL_NAMESPACE_BEGIN``) as a
# return type, so the namespace or type comes back as a ``function_definition``
# whose head holds the keyword as a stray identifier or ERROR token.
_CPP_SCOPE_KEYWORDS = frozenset({"namespace", "class", "struct", "union", "enum"})
# Name tokens a stray keyword is read as: ``field_identifier`` inside a class body.
_CPP_NAME_TOKENS = frozenset({"identifier", "field_identifier"})
# Declarators a misread scope can have: its bare name, qualified or not, or a
# stray ``template <...>`` (``MACRO template <typename T> class X {``). A real
# function wraps each of these in a function_declarator.
_CPP_SCOPE_DECLARATORS = _CPP_NAME_TOKENS | {"qualified_identifier", "template_function"}


def _keyword_token(node: Node) -> str | None:
    if node.type in _CPP_SCOPE_KEYWORDS:
        return node.type
    if node.type in _CPP_NAME_TOKENS and node.text is not None:
        word = node.text.decode("utf-8", errors="replace")
        return word if word in _CPP_SCOPE_KEYWORDS else None
    return None


def _macro_named_type_keyword(node: Node) -> str | None:
    """``class MACRO X {``: a bodiless specifier named after the macro, then the real name."""
    type_node = node.child_by_field_name("type")
    declarator = node.child_by_field_name("declarator")
    if type_node is None or type_node.type not in _CPP_TYPE_SPECIFIER_NODES:
        return None
    if declarator is None or declarator.type != "identifier":
        return None
    if type_node.child_by_field_name("body") is not None:
        return None
    return type_node.type.removesuffix("_specifier")


def misread_scope_keyword(node: Node) -> str | None:
    """The keyword of a namespace or type that tree-sitter read as a function.

    Two macro shapes: ``MACRO namespace x {`` / ``MACRO class X {`` (the
    keyword sits in the definition's head) and ``class MACRO X {`` (the macro
    reads as a bodiless class name and the real name as the declarator).
    None for every real function definition.
    """
    if node.type != "function_definition":
        return None
    macro_named = _macro_named_type_keyword(node)
    if macro_named is not None:
        return macro_named
    # A real function behind a macro (``API struct S *f()``) can also carry the
    # keyword in its head, but its declarator wraps a function_declarator.
    declarator = node.child_by_field_name("declarator")
    if declarator is None or declarator.type not in _CPP_SCOPE_DECLARATORS:
        return None
    keywords = (_keyword_token(token) for token in _head_tokens(node))
    return next((keyword for keyword in keywords if keyword is not None), None)


def _head_tokens(node: Node) -> Iterator[Node]:
    """The definition's children before its body, looking one level into ERROR nodes."""
    body = node.child_by_field_name("body")
    for child in node.children:
        if body is not None and child.id == body.id:
            return
        yield from (child.children if child.type == "ERROR" else (child,))


def _misread_namespace_ids(matches: list[dict]) -> frozenset[int]:
    """Misread namespaces enclosing any matched definition.

    Their contents are module-level declarations, so they must not count as
    the callable ancestor that drops a nested definition.
    """
    found: set[int] = set()
    seen: set[int] = set()
    def_nodes = (node for capture_dict in matches for node in capture_dict.get("symbol.def", []))
    for def_node in def_nodes:
        ancestor = def_node.parent
        while ancestor is not None and ancestor.id not in seen:
            seen.add(ancestor.id)
            if misread_scope_keyword(ancestor) == "namespace":
                found.add(ancestor.id)
            ancestor = ancestor.parent
    return frozenset(found)


@dataclass(frozen=True)
class _CppExportType:
    range_node: Node
    name: str
    is_forward_declaration: bool


def _is_bodiless_cpp_type(language: str, node_type: str, def_node: Node) -> bool:
    """True for a C/C++ type forward declaration such as ``class Env;``.

    tree-sitter gives it the same specifier node a definition uses, minus the
    ``body`` field, so without this it reads as a one-line definition.
    """
    if language not in ("cpp", "c"):
        return False
    if node_type == "template_declaration":
        # The wrapper never has a ``body`` field; only the type it wraps does.
        inner = next(
            (c for c in def_node.children if c.type in _CPP_TYPE_SPECIFIER_NODES),
            None,
        )
        return inner is not None and inner.child_by_field_name("body") is None
    if node_type not in _CPP_TYPE_SPECIFIER_NODES:
        return False
    if def_node.child_by_field_name("body") is not None:
        return False
    # ``typedef struct Handle Handle;`` dedups to the typedef name, a real API
    # symbol that must stay reportable, so a tag inside a typedef is never marked.
    parent = def_node.parent
    return parent is None or parent.type != "type_definition"


def _cpp_export_macro_parent(node: Node, parent_names: dict[int, str]) -> str | None:
    """Return the real type name for a member inside a macro-decorated C++ type."""
    ancestor = node.parent
    while ancestor is not None:
        parent_name = parent_names.get(ancestor.id)
        if parent_name is not None:
            return parent_name
        ancestor = ancestor.parent
    return None


@dataclass
class CppExportTypes:
    """Macro-decorated C++ types (``struct EXPORT Name``) recovered from one file.

    tree-sitter-cpp names such a type after the macro; cpp.scm marks the matches.
    """

    defs: dict[int, _CppExportType] = field(default_factory=dict)
    parents: dict[int, str] = field(default_factory=dict)
    capture_ids: set[int] = field(default_factory=set)
    macro_names: set[str] = field(default_factory=set)
    macro_def_ids: set[int] = field(default_factory=set)
    namespace_ids: frozenset[int] = frozenset()

    @cached_property
    def container_ids(self) -> frozenset[int]:
        """Misread nodes that hold declarations, not code: never a callable ancestor."""
        return frozenset(self.parents) | self.namespace_ids

    def add(
        self,
        def_node: Node,
        capture_node: Node,
        range_node: Node,
        name: str,
        *,
        is_forward_declaration: bool,
    ) -> None:
        self.defs[def_node.id] = _CppExportType(
            range_node=range_node,
            name=name,
            is_forward_declaration=is_forward_declaration,
        )
        self.capture_ids.add(capture_node.id)
        self.parents[capture_node.id] = name
        self.parents[range_node.id] = name


def collect_cpp_export_types(matches: list[dict], src: str) -> CppExportTypes:
    """Recover every macro-decorated type the query matched in one C++ file."""
    found = CppExportTypes(namespace_ids=_misread_namespace_ids(matches))
    cpp_export_matches = [
        capture_dict
        for capture_dict in matches
        if capture_dict.get("symbol.cpp_export_type", [])
    ]
    has_forward_candidate = any(
        capture_dict["symbol.cpp_export_type"][0].type
        in _CPP_EXPORT_FORWARD_DECLARATION_NODES
        for capture_dict in cpp_export_matches
    )
    cpp_macro_facts = (
        _build_cpp_macro_facts(matches, src) if has_forward_candidate else None
    )

    for capture_dict in cpp_export_matches:
        def_nodes = capture_dict.get("symbol.def", [])
        name_nodes = capture_dict.get("symbol.name", [])
        if not def_nodes or not name_nodes:
            continue
        type_name = _node_text(name_nodes[0], src)
        if not type_name:
            continue
        capture_node = capture_dict["symbol.cpp_export_type"][0]
        macro_nodes = capture_dict.get("symbol.cpp_export_macro", [])
        if capture_node.type not in _CPP_EXPORT_FORWARD_DECLARATION_NODES:
            # Body-form matches are unambiguous; suppress their macros by name.
            found.macro_names.update(
                _cpp_normalize_identifier(_node_text(node, src)) for node in macro_nodes
            )
            found.add(
                def_nodes[0], capture_node, capture_node, type_name, is_forward_declaration=False
            )
            continue
        active_macro_def = _forward_declaration_macro(
            cpp_macro_facts, macro_nodes, capture_node, src
        )
        if active_macro_def is None:
            continue
        found.add(
            def_nodes[0],
            capture_node,
            _forward_declaration_range(capture_node),
            type_name,
            is_forward_declaration=True,
        )
        found.macro_def_ids.add(active_macro_def.id)
    return found


def _forward_declaration_range(declaration: Node) -> Node:
    """The node a forward declaration spans, including a ``template <...>`` wrapper."""
    parent = declaration.parent
    if parent is not None and parent.type == "template_declaration":
        return parent
    return declaration


def _forward_declaration_macro(
    facts: _CppMacroFacts | None, macro_nodes: list[Node], declaration: Node, src: str
) -> Node | None:
    """The empty macro definition that makes a bodiless decorated type safe to recover."""
    if facts is None:
        return None
    macro_names = {
        _cpp_normalize_identifier(_node_text(node, src)) for node in macro_nodes
    } - {""}
    if len(macro_names) != 1:
        return None
    # A bodiless decorated type parses like ``struct Tag variable;``.
    return facts.empty_definition_at(next(iter(macro_names)), declaration)
