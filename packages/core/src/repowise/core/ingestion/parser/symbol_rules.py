"""Per-language symbol rules: names, kinds, line spans, decorators, modifiers, visibility."""

from __future__ import annotations

import sys

from tree_sitter import Node

from ..cpp_export_macros import _CppExportType, _is_bodiless_cpp_type
from ..extractors import (
    node_text,
    refine_elixir_call_kind,
    refine_fsharp_type_kind,
    refine_go_type_kind,
    refine_kotlin_class_kind,
    refine_pascal_type_kind,
    refine_swift_class_kind,
)
from ..extractors.bindings.ts_js import declarator_binds_callable, declarator_value_is_module_ref
from ..extractors.visibility import (
    refine_apex_visibility,
    refine_cpp_visibility,
    refine_csharp_visibility,
    refine_java_visibility,
    refine_py_visibility,
    refine_ruby_visibility,
    refine_rust_visibility,
    refine_ts_visibility,
)
from ..language_configs import LanguageConfig
from ..models import Symbol
from ..parser_helpers import (
    _elixir_is_template_definition,
    _elixir_symbol_name,
    _fsharp_binding_end_line,
    _fsharp_binding_has_params,
    _objc_is_macro_enum,
    _objc_symbol_name,
)
from ..python_overload import is_python_overload as _is_python_overload

_node_text = node_text

# Languages whose source reaches the parser as TypeScript/JavaScript. The
# SFC tags are here because ``sfc_source`` projects their <script> blocks into
# a TS buffer at identical offsets, so every TS/JS code path applies verbatim.
_TS_JS_LANGUAGES = ("typescript", "javascript", "svelte", "vue", "astro")


_TS_OVERLOAD_SIGNATURES = frozenset({"function_signature", "method_signature"})


def _keep_ts_overloads_beside_a_body(symbols: list[Symbol], node_types: list[str]) -> list[Symbol]:
    """Drop a bodiless TS signature whose id has no implementation in the file.

    That is an ambient declaration (``declare function``, a ``.d.ts``), not an
    overload, and it never was a symbol.
    """
    bodied = {
        s.id for s, t in zip(symbols, node_types, strict=True) if t not in _TS_OVERLOAD_SIGNATURES
    }
    return [
        s
        for s, t in zip(symbols, node_types, strict=True)
        if t not in _TS_OVERLOAD_SIGNATURES or s.id in bodied
    ]


def _drop_swift_extensions_of_local_types(symbols: list[Symbol]) -> list[Symbol]:
    """Drop an ``extension`` of a type the same file declares.

    It shares the type's id, so it would replace the type's node and kind; its
    members already name the type as their parent.
    """
    declared = {s.id for s in symbols if s.kind != "impl"}
    return [s for s in symbols if s.kind != "impl" or s.id not in declared]


def _is_declaration(
    def_node: Node,
    config: LanguageConfig,
    language: str,
    export_type: _CppExportType | None,
    src: str,
) -> bool:
    node_type = def_node.type
    if node_type in config.declaration_node_types:
        return True
    if language == "python":
        return _is_python_overload(def_node, src)
    if export_type is not None:
        return export_type.is_forward_declaration
    return _is_bodiless_cpp_type(language, node_type, def_node)


_FSHARP_BINDING_NODE_TYPES = ("function_declaration_left", "value_declaration_left")
_DART_FUNCTION_NODE_TYPES = ("function_signature", "getter_signature", "setter_signature")


def _is_fsharp_binding(language: str, node_type: str) -> bool:
    return language == "fsharp" and node_type in _FSHARP_BINDING_NODE_TYPES


def _language_symbol_name(language: str, def_node: Node, name: str, src: str) -> str | None:
    """Apply a language's own naming rule; None drops the match."""
    if not name:
        return None
    if language == "elixir":
        # A `def` inside `quote do ... end` is macro body, not a
        # definition of the module that writes it.
        if _elixir_is_template_definition(def_node, src):
            return None
        # `defimpl Proto, for: Type` is named for the module the
        # compiler generates, not for the protocol alone.
        return _elixir_symbol_name(def_node, name, src)
    if language == "objectivec":
        # `typedef NS_ENUM(NSInteger, Kind) { ... }` has no grammar
        # rule, so only its enum *cases* survive as declarators and
        # each would become a symbol named as though it were the type.
        if _objc_is_macro_enum(def_node, src):
            return None
        # A method is named by its whole selector
        # (`initWithName:age:`), which no single node holds, and a
        # category by the class it extends plus its own name.
        return _objc_symbol_name(def_node, name, src)
    return name


# ``(language, node_type)`` whose "class" kind the body refines: Kotlin
# interface / enum class share class_declaration, Pascal ``declType`` wraps
# class, record, interface, enum and more, and F# writes a class, a struct
# and an interface with the same ``anon_type_defn`` node.
_CLASS_KIND_REFINERS = {
    ("kotlin", "class_declaration"): refine_kotlin_class_kind,
    ("swift", "class_declaration"): refine_swift_class_kind,
    ("pascal", "declType"): refine_pascal_type_kind,
    ("fsharp", "anon_type_defn"): refine_fsharp_type_kind,
}


def _refine_symbol_kind(
    kind: str, def_node: Node, config: LanguageConfig, language: str, src: str
) -> str:
    """Narrow a node-type kind where one node shape spells several kinds."""
    node_type = def_node.type
    # A value binding that carries parameter patterns beside its
    # name is a function the grammar reparsed as a value because
    # of its return-type annotation.
    if (
        language == "fsharp"
        and node_type == "value_declaration_left"
        and _fsharp_binding_has_params(def_node)
    ):
        kind = "function"

    # Refine "struct" kind for Go type_spec (check if struct or interface body)
    if kind == "struct" and config.parent_extraction == "receiver":
        kind = refine_go_type_kind(def_node, src)

    if kind == "class":
        refine = _CLASS_KIND_REFINERS.get((language, node_type))
        if refine is not None:
            kind = refine(def_node)

    # Elixir: every definition is a ``call``; its keyword names the kind.
    if language == "elixir" and node_type == "call":
        kind = refine_elixir_call_kind(def_node, src)
    return kind


def _symbol_end_line(
    def_node: Node,
    config: LanguageConfig,
    language: str,
    export_type: _CppExportType | None,
) -> int | None:
    """The symbol's last line, or None for a Dart local function (not a symbol)."""
    node_type = def_node.type
    end_line = def_node.end_point[0] + 1
    if config.symbol_end_line_fn is not None:
        end_line = config.symbol_end_line_fn(def_node, end_line)
    if export_type is not None:
        end_line = export_type.range_node.end_point[0] + 1
    # F#: the captured left-hand side stops at the parameters; extend over the body.
    if _is_fsharp_binding(language, node_type):
        end_line = _fsharp_binding_end_line(def_node)
    if language == "dart" and node_type in _DART_FUNCTION_NODE_TYPES:
        return _dart_function_end_line(def_node, end_line)
    return end_line


def _dart_function_end_line(def_node: Node, end_line: int) -> int | None:
    """Dart: a function's body is a sibling of its signature.

    Local functions nested inside another function's body have no callable
    *ancestor* (the enclosing signature is a sibling), so they are dropped
    here (None); and the line range extends to the trailing body sibling or
    call-site attribution stops at the signature line.
    """
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type in ("function_body", "function_expression"):
            return None
        ancestor = ancestor.parent
    anchor = def_node
    if def_node.parent is not None and def_node.parent.type == "method_signature":
        anchor = def_node.parent
    body_sibling = anchor.next_named_sibling
    if body_sibling is not None and body_sibling.type == "function_body":
        return body_sibling.end_point[0] + 1
    return end_line


def _module_binding_kind(def_node: Node, name: str, language: str, src: str) -> str | None:
    """Kind of a module-level assignment, or None when it only binds a module.

    SCREAMING_CASE names are constants by convention; the rest are module
    variables (singletons like ``app = FastAPI()``, registries, caches).
    ``str.isupper()`` requires at least one cased char, so names with no
    letters (``_``, ``__all__``) fall to "variable" rather than being
    mislabelled constants by ``name == name.upper()``.
    """
    # TS/JS: ``const svc = require('./svc')`` binds a module (already an
    # import), so it is not a symbol.
    if language in _TS_JS_LANGUAGES and declarator_value_is_module_ref(def_node, src):
        return None
    # A structurally callable value is a function whatever its name says.
    callable_kind = (
        declarator_binds_callable(def_node, src) if language in _TS_JS_LANGUAGES else None
    )
    return callable_kind or ("constant" if name.isupper() else "variable")


def _attribute_decorators(def_node: Node, language: str, src: str) -> list[str]:
    """Attribute texts that act as decorators (Rust, C#, C/C++), brackets stripped.

    They land beside ``@`` decorators so one deprecation check reads them all.
    """
    if language == "rust":
        return _rust_outer_attributes(def_node, src)
    # C#: ``attribute_list`` is child[0] of the declaration itself, not a
    # preceding sibling in the class body.
    if language == "csharp":
        return _leading_child_attributes(def_node, "attribute_list", "[", "]", src)
    # C/C++: [[deprecated]] / [[deprecated("reason")]] are
    # ``attribute_declaration`` nodes, child[0] of function_definition itself
    # (NOT a preceding sibling at translation_unit level).
    if language in ("cpp", "c"):
        return _leading_child_attributes(def_node, "attribute_declaration", "[[", "]]", src)
    if language == "kotlin":
        return _kotlin_detached_annotation(def_node, src)
    return []


def _kotlin_detached_annotation(def_node: Node, src: str) -> list[str]:
    """Kotlin: annotations the grammar split off a top-level declaration.

    When the last annotation on its own line before a bare ``fun`` or
    ``object`` takes one argument (``@Suppress("unused")``), the argument list
    reads as a parenthesized expression: the annotations become an
    ``annotated_expression`` chain beside a declaration with no modifiers.
    """
    if def_node.parent is None or def_node.parent.type != "source_file":
        return []
    sib = def_node.prev_sibling
    while sib is not None and sib.type in ("line_comment", "block_comment"):
        sib = sib.prev_sibling
    return [_node_text(sib, src).strip()] if _is_split_annotation_chain(sib) else []


def _is_split_annotation_chain(node: Node | None) -> bool:
    """Nested ``annotated_expression`` nodes of annotations, the last one's argument at the tail."""
    while node is not None and node.type == "annotated_expression":
        kinds = [c.type for c in node.named_children]
        if len(kinds) != 2 or kinds[0] != "annotation":
            return False
        if kinds[1] == "parenthesized_expression":
            return True
        node = node.named_children[1]
    return False


def _restore_kotlin_annotations(
    symbols: list[Symbol],
    recovered: dict[int, tuple[int, str]],
) -> None:
    """Restore annotation lines and decorator text on recovered Kotlin symbols."""
    for symbol in symbols:
        entry = recovered.get(symbol.start_line)
        if entry is not None and symbol.parent_name is None:
            symbol.start_line, text = entry
            symbol.decorators = [text, *symbol.decorators]


def _declared_modifiers(def_node: Node, node_types: frozenset[str], src: str) -> tuple[str, ...]:
    """Lowercased keyword modifiers written in *def_node*'s modifier children.

    Keywords are the anonymous leaves of those children (C# ``modifier`` >
    ``override``, Kotlin ``modifiers`` > ``member_modifier`` > ``override``) or
    a named leaf that is itself a modifier (VB.NET ``modifier`` ``Overrides``).
    Named leaves such as Scala's ``protected[this]`` qualifier are not
    keywords. Annotations and attributes nested among them are skipped: they
    are decorators, not modifiers. The words are interned: a handful of
    distinct values repeat on every symbol.
    """
    if not node_types:
        return ()
    words: list[str] = []
    stack = [c for c in reversed(def_node.children) if c.type in node_types]
    while stack:
        node = stack.pop()
        if "annotation" in node.type or "attribute" in node.type:
            continue
        if node.child_count == 0:
            text = _node_text(node, src).strip()
            keyword = not node.is_named or "modifier" in node.type
            if keyword and text.isalpha():
                words.append(sys.intern(text.lower()))
            continue
        stack.extend(reversed(node.children))
    return tuple(words)


# Kotlin has no ``static`` keyword: a member of an ``object`` or of a
# ``companion object`` is the language's static member.
_KOTLIN_STATIC_OWNERS = frozenset({"companion_object", "object_declaration"})
_KOTLIN_CLASS_OWNERS = frozenset({"class_declaration", "object_literal"})


def _declared_or_implied_modifiers(
    def_node: Node, node_types: frozenset[str], language: str, src: str
) -> tuple[str, ...]:
    """``_declared_modifiers`` plus ``static`` for a Kotlin ``object`` /
    ``companion object`` function, which the grammar does not spell out."""
    modifiers = _declared_modifiers(def_node, node_types, src)
    if (
        language == "kotlin"
        and def_node.type == "function_declaration"
        and "static" not in modifiers
        and _in_kotlin_static_owner(def_node)
    ):
        return (*modifiers, "static")
    return modifiers


def _in_kotlin_static_owner(def_node: Node) -> bool:
    """Whether the nearest enclosing type of *def_node* is an ``object`` or a
    ``companion object`` (so the function has no instance of its own)."""
    node = def_node.parent
    while node is not None:
        if node.type in _KOTLIN_STATIC_OWNERS:
            return True
        if node.type in _KOTLIN_CLASS_OWNERS:
            return False
        node = node.parent
    return False


def _rust_outer_attributes(def_node: Node, src: str) -> list[str]:
    """Rust: outer attributes (#[...]) are preceding siblings of the item."""
    attrs: list[str] = []
    if def_node.parent is None:
        return attrs
    siblings = def_node.parent.children
    for j, sib in enumerate(siblings):
        if sib.id != def_node.id:
            continue
        k = j - 1
        while k >= 0 and siblings[k].type == "attribute_item":
            attr_text = _node_text(siblings[k], src).strip()
            # Strip #[ and ] to get the inner attribute text
            if attr_text.startswith("#[") and attr_text.endswith("]"):
                attrs.append(attr_text[2:-1])
            k -= 1
        break
    return attrs


def _leading_child_attributes(
    def_node: Node, node_type: str, opener: str, closer: str, src: str
) -> list[str]:
    """Inner text of the attribute children leading *def_node*, until the first other child.

    A comment between attribute lists does not end the run, so an attribute
    placed after one is still recorded.
    """
    attrs: list[str] = []
    for child in def_node.children:
        if child.type != node_type:
            if child.type == "comment":
                continue
            break
        attr_text = _node_text(child, src).strip()
        if attr_text.startswith(opener) and attr_text.endswith(closer):
            attrs.append(attr_text[len(opener) : -len(closer)])
    return attrs


def _refine_visibility(
    def_node: Node,
    language: str,
    visibility: str,
    name: str,
    ts_deferred_exports: frozenset[str] | None,
    py_all_names: frozenset[str] | None,
    src: str,
) -> tuple[str, bool]:
    """``(visibility, is_exported_symbol)`` after the language's AST-context rules."""
    # C/C++ (and plain C inside Objective-C) visibility comes from AST
    # context: access specifiers, storage class, export attributes.
    if language in ("cpp", "c", "objectivec"):
        return refine_cpp_visibility(def_node, visibility, src)
    # C#: an unmodified declaration's default depends on what encloses
    # it, which the modifier-text fn cannot see.
    if language == "csharp":
        return refine_csharp_visibility(def_node, visibility), False
    # Java: no access keyword means package-private, except inside an
    # interface or annotation body, which the modifier-text fn cannot see.
    if language == "java":
        return refine_java_visibility(def_node), False
    # Apex: the keyword is read from the AST, where annotation text cannot
    # pass for one, and an interface member is public.
    if language == "apex":
        return refine_apex_visibility(def_node), False
    # TS/JS: a top-level declaration is only public when exported —
    # inline, via ``export { x }`` lists, or ``export default x``.
    if language in _TS_JS_LANGUAGES:
        return refine_ts_visibility(def_node, visibility, name, ts_deferred_exports), False
    # Python: a literal ``__all__`` raises the names it lists to public; it
    # never demotes the ones it omits. GDScript shares ``py_visibility`` but
    # has no ``__all__``, so the gate is on the language, not the fn.
    if language == "python":
        return refine_py_visibility(def_node, visibility, name, py_all_names), False
    # Rust: a trait's items may not write ``pub`` of their own, so the
    # trait's modifier is the only place their visibility is stated.
    if language == "rust":
        return refine_rust_visibility(def_node, visibility, src), False
    # Ruby: ``private`` / ``protected`` are calls in the class body, so the
    # def's own text says nothing; what precedes and follows it does.
    if language == "ruby":
        return refine_ruby_visibility(def_node, visibility, src), False
    return visibility, False


def _dart_mixin_parent(def_node: Node, config: LanguageConfig, src: str) -> str | None:
    """Name of the Dart ``mixin`` enclosing *def_node*, if it is the nearest type."""
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type == "mixin_declaration":
            ident = next((c for c in ancestor.children if c.type == "identifier"), None)
            return _node_text(ident, src) if ident is not None else None
        if ancestor.type in config.parent_class_types:
            return None
        ancestor = ancestor.parent
    return None
