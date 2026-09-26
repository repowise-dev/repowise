"""C/C++ export-macro recovery: macro-decorated types and forward-declaration helpers."""

from __future__ import annotations

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

    @cached_property
    def parent_ids(self) -> frozenset[int]:
        return frozenset(self.parents)

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
    found = CppExportTypes()
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
