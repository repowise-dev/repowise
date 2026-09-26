"""C/C++ export-macro recovery: macro state facts and forward-declaration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from tree_sitter import Node

from .cpp_macro_facts import _build_cpp_macro_facts, _cpp_normalize_identifier, _CppMacroFacts
from .extractors import node_text as _node_text

# C/C++ node types that spell a type. Each one covers both the definition and
# the forward declaration of that type; only the ``body`` field tells them
# apart. See ``_is_bodiless_cpp_type``.
# ``union_specifier`` is absent because neither grammar's query captures one
# as a symbol today. If a union capture is ever added, add it here too, or a
# bodiless ``union U;`` goes back to reading as a definition.
_CPP_TYPE_SPECIFIER_NODES = frozenset({"class_specifier", "struct_specifier", "enum_specifier"})
_CPP_EXPORT_FORWARD_DECLARATION_NODES = frozenset({"declaration", "field_declaration"})


@dataclass(frozen=True)
class _CppExportType:
    range_node: Node
    name: str
    is_forward_declaration: bool


def _is_bodiless_cpp_type(language: str, node_type: str, def_node: Node) -> bool:
    """True for a C/C++ type forward declaration such as ``class Env;``.

    ``declaration_node_types`` already catches a *function* prototype, whose
    tree-sitter node genuinely is a ``declaration``. A type forward declaration
    is not: it arrives as the very same ``class_specifier`` a definition uses,
    minus the ``body`` field. Left unmarked it reads as a whole class defined
    on one line, so every consumer that distinguishes a declaration from a
    definition — call resolution's declaration/definition pairing and the
    dead-code passes — silently treats the header line as the real thing.
    """
    if language not in ("cpp", "c"):
        return False
    if node_type == "template_declaration":
        # ``template <typename T> class Foo;``. The wrapper carries no ``body``
        # field of its own — the inner specifier does — so asking the wrapper
        # would call every template a declaration. Ask the type it wraps.
        inner = next(
            (c for c in def_node.children if c.type in _CPP_TYPE_SPECIFIER_NODES),
            None,
        )
        return inner is not None and inner.child_by_field_name("body") is None
    if node_type not in _CPP_TYPE_SPECIFIER_NODES:
        return False
    if def_node.child_by_field_name("body") is not None:
        return False
    # ``typedef struct CBMAutomaton CBMAutomaton;`` — C's opaque-handle idiom.
    # The tag and the typedef name are the same identifier, so both patterns
    # match at one position and dedup keeps a single symbol. That symbol is the
    # typedef name, which *is* a deletable API artifact and must stay
    # reportable. Erring toward under-marking: a forward-declared tag under a
    # differently-named typedef (``typedef struct Impl_s Handle;``) is left
    # unmarked too, which only costs a suppression we never had.
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
        type_nodes = capture_dict.get("symbol.cpp_export_type", [])
        def_nodes = capture_dict.get("symbol.def", [])
        name_nodes = capture_dict.get("symbol.name", [])
        macro_nodes = capture_dict.get("symbol.cpp_export_macro", [])
        if not type_nodes or not def_nodes or not name_nodes:
            continue
        type_name = _node_text(name_nodes[0], src)
        if not type_name:
            continue
        capture_node = type_nodes[0]
        is_forward_declaration = capture_node.type in _CPP_EXPORT_FORWARD_DECLARATION_NODES
        range_node = capture_node
        if (
            is_forward_declaration
            and capture_node.parent is not None
            and capture_node.parent.type == "template_declaration"
        ):
            range_node = capture_node.parent
        active_macro_def: Node | None = None
        if is_forward_declaration:
            active_macro_def = _forward_declaration_macro(
                cpp_macro_facts, macro_nodes, capture_node, src
            )
            if active_macro_def is None:
                continue
        else:
            # Body-form matches are unambiguous. Preserve #1896's
            # name-based suppression across conditional definitions.
            found.macro_names.update(
                _cpp_normalize_identifier(_node_text(node, src)) for node in macro_nodes
            )
        found.defs[def_nodes[0].id] = _CppExportType(
            range_node=range_node,
            name=type_name,
            is_forward_declaration=is_forward_declaration,
        )
        found.capture_ids.add(capture_node.id)
        found.parents[capture_node.id] = type_name
        found.parents[range_node.id] = type_name
        if active_macro_def is not None:
            found.macro_def_ids.add(active_macro_def.id)
    return found


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
    # A bodiless decorated type is syntactically identical to
    # an ordinary ``struct Tag variable;`` declaration.
    return facts.empty_definition_at(next(iter(macro_names)), declaration)
