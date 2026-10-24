"""Symbol extraction: one query match to one ``Symbol``, and the parent it belongs to."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from tree_sitter import Node

from ..cpp_export_macros import (
    CppExportTypes,
    _cpp_export_macro_parent,
    _cpp_normalize_identifier,
    _CppExportType,
    collect_cpp_export_types,
)
from ..extractors import (
    build_signature,
    extract_go_receiver_type,
    extract_symbol_docstring,
    node_text,
)
from ..extractors.cobol import cobol_condition_parent
from ..extractors.synthetic_symbols import extract_synthetic_symbols
from ..extractors.synthetic_symbols.csharp_extension_blocks import drop_extension_block_symbols
from ..extractors.visibility import py_module_all_names, ts_deferred_export_names
from ..language_configs import LanguageConfig
from ..models import FileInfo, Symbol
from ..parser_helpers import (
    _build_qualified_name,
    _csharp_type_parameter_count,
    _dedupe_objc_interface_symbols,
    _dedupe_pascal_interface_symbols,
    _elixir_module_parent,
    _fsharp_binding_is_nested,
    _fsharp_parent_is_type,
    _fsharp_parent_name,
    _has_callable_ancestor,
    _is_async_node,
    _kotlin_receiver_fields,
    _objc_container_node,
    _objc_container_parent,
    _qualified_cpp_parent,
    _qualified_pascal_parent,
    _ts_nested_object_method_owner,
    follow_renamed_hosts,
    in_anonymous_class,
    is_anonymous_member,
    scope_segment,
)
from ..symbol_identity import disambiguate_colliding_ids, symbol_discriminator
from .symbol_rules import (
    _TS_JS_LANGUAGES,
    _attribute_decorators,
    _dart_mixin_parent,
    _declared_or_implied_modifiers,
    _drop_swift_extensions_of_local_types,
    _is_declaration,
    _is_fsharp_binding,
    _keep_ts_overloads_beside_a_body,
    _language_symbol_name,
    _module_binding_kind,
    _refine_symbol_kind,
    _refine_visibility,
    _symbol_end_line,
)

_node_text = node_text

# Node types whose .scm patterns are anchored at module level, so the
# callable-ancestor filter must not apply to them.
_MODULE_ANCHORED_NODE_TYPES = frozenset({"assignment", "variable_declarator"})


def _normalize_newlines(text: str | None) -> str | None:
    """Return *text* with CRLF line endings rewritten as LF.

    The signature and docstring strings are sliced straight out of the file
    bytes, so a repository committed with CRLF — or a Windows checkout with
    ``core.autocrlf=true`` — stores literal ``\r\n`` in them. The same
    symbol then reads differently depending on the checkout's line-ending
    setting, and the extra carriage returns cost tokens on every MCP reply.

    Normalising the source before parsing is not an option: tree-sitter byte
    offsets and line numbers have to keep matching the real file. So it
    happens here, where the stored strings are produced. A lone ``\r`` is
    not a line ending and is left alone.

    *None* passes straight through: the docstring helpers return it when a
    symbol has no docstring, and that absence is information.
    """
    if text is None:
        return None
    return text.replace("\r\n", "\n")


def _match_identity(
    capture_dict: dict,
    config: LanguageConfig,
    language: str,
    src: str,
    cpp_exports: CppExportTypes,
    seen: set[tuple[int, str]],
) -> tuple[Node, list[Node], str, _CppExportType | None, int] | None:
    """``(def_node, name_nodes, name, export_type, start_line)`` for a new symbol match.

    None drops the match: no definition or name, a recovery match this file
    does not support, a suppressed export macro, or a duplicate. The
    ``(start_line, name)`` key is recorded in *seen* here.
    """
    def_nodes = capture_dict.get("symbol.def", [])
    name_nodes = capture_dict.get("symbol.name", [])
    captured_export_type_nodes = capture_dict.get("symbol.cpp_export_type", [])

    if (
        captured_export_type_nodes
        and captured_export_type_nodes[0].id not in cpp_exports.capture_ids
    ):
        # The query also sees ordinary ``struct Tag variable;`` forms;
        # discard only the unsupported recovery match.
        return None

    if not def_nodes or not name_nodes:
        return None

    def_node = def_nodes[0]
    name = _language_symbol_name(
        language, def_node, config.symbol_name_fn(_node_text(name_nodes[0], src), def_node.type), src
    )
    if name is None:
        return None

    export_type = cpp_exports.defs.get(def_node.id)
    if export_type is not None and name != export_type.name:
        # The ordinary struct/class query sees the same specifier, but
        # tree-sitter calls the export macro its name. Keep only the
        # dedicated match whose name is the outer declarator.
        return None

    if def_node.type == "preproc_def" and (
        _cpp_normalize_identifier(name) in cpp_exports.macro_names
        or def_node.id in cpp_exports.macro_def_ids
    ):
        # Body-form macros are suppressed by name;
        # ambiguous forward declarations suppress only the exact
        # active definition that made recovery safe.
        return None

    start_line = def_node.start_point[0] + 1
    if export_type is not None:
        start_line = export_type.range_node.start_point[0] + 1
    dedup_key = (start_line, name)
    if dedup_key in seen:
        return None
    seen.add(dedup_key)
    return def_node, name_nodes, name, export_type, start_line


def _parent_type_name(ancestor: Node, src: str) -> str | None:
    """The name a class/impl ancestor gives its members, without type arguments."""
    name_node = ancestor.child_by_field_name("name") or (
        ancestor.child_by_field_name("type")  # Rust impl_item
    )
    if name_node is None:
        return None
    # For Rust impl blocks with generic types (e.g. impl<T> Foo<T>),
    # extract only the base type name, not the full generic signature.
    if name_node.type in ("generic_type", "scoped_type_identifier"):
        inner = name_node.child_by_field_name(
            "type" if name_node.type == "generic_type" else "name"
        )
        if inner and inner.type == "type_identifier":
            name_node = inner
    return _node_text(name_node, src)


def _nesting_parent(def_node: Node, config: LanguageConfig, src: str) -> str | None:
    """The class/impl ancestor naming *def_node*'s parent.

    An anonymous class is a class of its own, so its members gain segments
    below it: ``Gson::FILTER::Filter::check``.
    """
    segments: list[str] = []
    child, ancestor = def_node, def_node.parent
    while ancestor is not None:
        if ancestor.type in config.parent_class_types:
            name = _parent_type_name(ancestor, src)
            if name is not None:
                return "::".join([name, *reversed(segments)])
        else:
            segment = scope_segment(
                ancestor, child, bool(segments), config.anonymous_class_types, src
            )
            if segment is not None:
                segments.append(segment)
        child, ancestor = ancestor, ancestor.parent
    # A Kotlin top-level function's object expression: ``f::Foo``.
    return "::".join(reversed(segments)) or None


def _java_enum_constant(def_node: Node, config: LanguageConfig, src: str) -> str | None:
    """The enum constant whose body declares *def_node*, before any enclosing type.

    An anonymous class is such a type: ``_find_parent`` names its members.
    """
    ancestor = def_node.parent
    while (
        ancestor is not None
        and ancestor.type not in config.parent_class_types
        and ancestor.type not in config.anonymous_class_types
    ):
        if ancestor.type == "enum_constant":
            name = ancestor.child_by_field_name("name")
            return _node_text(name, src) if name else None
        ancestor = ancestor.parent
    return None


def _extract_symbols(
    matches: list[dict],
    config: LanguageConfig,
    file_info: FileInfo,
    src: str,
) -> list[Symbol]:
    language = file_info.language
    symbols: list[Symbol] = []
    # Parallel to ``symbols`` (same indices) -- consumed for Pascal and
    # Objective-C, to dedupe interface-declaration vs. implementation
    # method pairs after the loop, and for TS overload signatures. See
    # _dedupe_pascal_interface_symbols / _dedupe_objc_interface_symbols.
    node_types: list[str] = []
    # Also parallel to ``symbols``, Objective-C only: which of @interface /
    # @implementation / @protocol declared each member, so a protocol's
    # method is never deduped against a same-named class method.
    objc_container_kinds: list[str | None] = []

    # Deferred-export names (``export { x }`` / ``export default x``),
    # computed once per file for the TS/JS visibility refinement.
    ts_deferred_exports: frozenset[str] | None = None
    if language in _TS_JS_LANGUAGES:
        ts_deferred_exports = ts_deferred_export_names(src)
    # Literal module ``__all__`` names, once per file for the Python
    # refinement. None means no signal (built at runtime or absent).
    py_all_names: frozenset[str] | None = None
    if language == "python":
        py_all_names = py_module_all_names(src)
    cpp_exports = (
        collect_cpp_export_types(matches, src) if language == "cpp" else CppExportTypes()
    )

    file = _SymbolPass(config, file_info, src, cpp_exports, ts_deferred_exports, py_all_names)
    for capture_dict in matches:
        built = _symbol_from_match(capture_dict, file)
        if built is None:
            continue
        symbol, def_node = built
        symbols.append(symbol)
        node_types.append(def_node.type)

        if language == "python" and def_node.type == "assignment":
            for alias_symbol in _python_assignment_alias_symbols(
                symbol, def_node, file_info.path, src
            ):
                symbols.append(alias_symbol)
                node_types.append(def_node.type)

        if language == "objectivec":
            container = _objc_container_node(def_node, config.parent_class_types)
            objc_container_kinds.append(container.type if container else None)

    if language == "pascal":
        symbols = _dedupe_pascal_interface_symbols(symbols, node_types)

    if language in _TS_JS_LANGUAGES:
        symbols = _keep_ts_overloads_beside_a_body(symbols, node_types)

    if language == "swift":
        symbols = _drop_swift_extensions_of_local_types(symbols)

    # A .m file routinely declares its private methods in a class
    # extension and defines them below in the @implementation, which
    # builds each symbol id twice in one file.
    if language == "objectivec":
        symbols = _dedupe_objc_interface_symbols(symbols, node_types, objc_container_kinds)

    return symbols

@dataclass
class _SymbolPass:
    """Per-file inputs every symbol match of one file is read against."""

    config: LanguageConfig
    file_info: FileInfo
    src: str
    cpp_exports: CppExportTypes
    ts_deferred_exports: frozenset[str] | None
    py_all_names: frozenset[str] | None
    # (start_line, name) of every match kept so far: dedups decorated twins.
    seen: set[tuple[int, str]] = field(default_factory=set)


def _symbol_from_match(capture_dict: dict, file: _SymbolPass) -> tuple[Symbol, Node] | None:
    """One query match as a symbol and its definition node, or None to drop it.

    Records the match's ``(start_line, name)`` in ``file.seen`` once it passes
    the name checks, so a later duplicate is dropped even if this one is.
    """
    config, file_info, src = file.config, file.file_info, file.src
    language = file_info.language
    identity = _match_identity(capture_dict, config, language, src, file.cpp_exports, file.seen)
    if identity is None:
        return None
    def_node, name_nodes, name, export_type, start_line = identity

    node_type = def_node.type
    # A TS/JS object-literal method inside an anonymous callback is kept,
    # qualified by the binding that owns its object.
    object_owner = (
        _ts_nested_object_method_owner(def_node, src, config.symbol_node_types)
        if language in _TS_JS_LANGUAGES
        else None
    )
    spanned = _kind_and_end_line(def_node, name, export_type, object_owner is not None, file)
    if spanned is None:
        return None
    kind, end_line = spanned

    modifier_texts = [_node_text(m, src) for m in capture_dict.get("symbol.modifiers", [])]
    visibility, is_exported_symbol = _refine_visibility(
        def_node,
        language,
        config.visibility_fn(name, modifier_texts),
        name,
        file.ts_deferred_exports,
        file.py_all_names,
        src,
    )

    parent_name = object_owner or _resolve_parent_name(
        def_node,
        config,
        capture_dict.get("symbol.receiver", []),
        name_nodes,
        language,
        src,
        no_export_type=export_type is None,
        export_type_parents=file.cpp_exports.parents,
    )

    # Anonymous Go interface methods have no named owner and are not symbols.
    if node_type == "method_elem" and parent_name is None:
        return None

    # A ``field_declaration`` cannot occur outside a class body, so a
    # missing parent means the class did not parse. Grammar recovery,
    # not a member function.
    if node_type == "function_declarator" and parent_name is None:
        return None

    params_nodes = capture_dict.get("symbol.params", [])
    params_text = _node_text(params_nodes[0], src) if params_nodes else ""
    scope = _id_scope(def_node, parent_name, config, language, src)
    sym_id = f"{file_info.path}::{scope}::{name}" if scope else f"{file_info.path}::{name}"
    symbol = Symbol(
        id=sym_id,
        name=name,
        qualified_name=_build_qualified_name(file_info.path, scope, name),
        kind=_method_or_kind(kind, parent_name, def_node, language),  # type: ignore[arg-type]
        signature=_normalize_newlines(
            build_signature(node_type, name, params_text, def_node, src)
        ) or "",
        start_line=start_line,
        end_line=end_line,
        docstring=_normalize_newlines(
            extract_symbol_docstring(def_node, src, language)
        ),
        decorators=(
            [m for m in modifier_texts if m.startswith("@")]
            + _attribute_decorators(def_node, language, src)
        ),
        visibility=visibility,  # type: ignore[arg-type]
        is_async=_is_async_node(def_node, src),
        language=language,
        parent_name=parent_name,
        is_exported_symbol=is_exported_symbol,
        is_declaration=_is_declaration(def_node, config, language, export_type, src),
        type_parameter_count=(
            _csharp_type_parameter_count(def_node) if language == "csharp" else None
        ),
        **_kotlin_receiver_fields(def_node, params_nodes, language, name),
        modifiers=_declared_or_implied_modifiers(
            def_node, config.modifier_node_types, language, src
        ),
    )
    return symbol, def_node


def _kind_and_end_line(
    def_node: Node,
    name: str,
    export_type: _CppExportType | None,
    has_object_owner: bool,
    file: _SymbolPass,
) -> tuple[str, int] | None:
    """``(kind, end_line)`` of a match, or None when it is not a symbol here."""
    config, language, src = file.config, file.file_info.language, file.src
    kind = _symbol_kind(
        def_node,
        config,
        language,
        src,
        file.cpp_exports.container_ids,
        # An anonymous class member is a method of its own class, even
        # inside another method's body; ``_find_parent`` names it.
        keep_nested=has_object_owner or in_anonymous_class(def_node, config.anonymous_class_types),
    )
    if kind is None:
        return None

    end_line = _symbol_end_line(def_node, config, language, export_type)
    if end_line is None:
        return None

    if def_node.type in _MODULE_ANCHORED_NODE_TYPES:
        kind = _module_binding_kind(def_node, name, language, src)
        if kind is None:
            return None
    return kind, end_line


def _method_or_kind(kind: str, parent_name: str | None, def_node: Node, language: str) -> str:
    """Upgrade a function with a parent to a method.

    F#: a nested module is a parent too (for id uniqueness), but it is not a
    type, so a ``let`` inside one stays a function.
    """
    if parent_name and kind == "function" and (
        language != "fsharp" or _fsharp_parent_is_type(def_node)
    ):
        return "method"
    return kind


def _id_scope(
    def_node: Node, parent_name: str | None, config: LanguageConfig, language: str, src: str
) -> str | None:
    """The id scope: the parent, plus a Java enum constant whose body declares the symbol.

    Each constant's override is then its own node while the enum stays its owner.
    """
    constant = _java_enum_constant(def_node, config, src) if language == "java" else None
    return f"{parent_name}::{constant}" if parent_name and constant else parent_name


def _symbol_kind(
    def_node: Node,
    config: LanguageConfig,
    language: str,
    src: str,
    container_ids: frozenset[int],
    *,
    keep_nested: bool = False,
) -> str | None:
    """The symbol kind for *def_node*, or None when it is not a symbol here."""
    node_type = def_node.type
    kind = config.symbol_node_types.get(node_type)
    if kind is None:
        return None

    # Only module-level and class-body members are symbols; the query is
    # recursive, so defs nested in a callable are dropped. Module-anchored
    # node types only match at module level and skip the check, as does a
    # def the caller has placed already (``keep_nested``).
    if (
        not keep_nested
        and node_type not in _MODULE_ANCHORED_NODE_TYPES
        and _has_callable_ancestor(def_node, config.symbol_node_types, container_ids)
    ):
        return None

    # F#: a ``let`` nested in another binding has no callable ancestor, so
    # it is checked separately. A ``let`` in a type body is a field and stays.
    if _is_fsharp_binding(language, node_type) and _fsharp_binding_is_nested(def_node):
        return None
    return _refine_symbol_kind(kind, def_node, config, language, src)


def _resolve_parent_name(
    def_node: Node,
    config: LanguageConfig,
    receiver_nodes: list[Node],
    name_nodes: list[Node],
    language: str,
    src: str,
    *,
    no_export_type: bool,
    export_type_parents: dict[int, str],
) -> str | None:
    """The enclosing type or module name, trying each language's own shape."""
    parent_name = _find_parent(def_node, config, receiver_nodes, src)
    if parent_name is not None:
        return parent_name

    if language == "cpp" and no_export_type:
        parent_name = _cpp_export_macro_parent(def_node, export_type_parents)
        if parent_name is not None:
            return parent_name

    # Dart mixin_declaration exposes no ``name`` field, so
    # ``_find_parent``'s field lookup misses mixin members.
    if language == "dart":
        return _dart_mixin_parent(def_node, config, src)

    # F#: no type node carries a ``name`` field -- the name hangs off
    # a ``type_name`` child -- so the generic walk finds the ancestor
    # and then reads nothing off it.
    if language == "fsharp":
        return _fsharp_parent_name(def_node, src)

    # C/C++ out-of-line ``void Foo::method()`` names its class in the scope.
    if language in ("cpp", "c") and name_nodes:
        return _qualified_cpp_parent(name_nodes[0], src)

    # Pascal out-of-line ``function TFoo.Bar`` names its class in the header.
    if language == "pascal" and name_nodes:
        return _qualified_pascal_parent(name_nodes[0], src)

    # Elixir: ``defmodule`` is a ``call``; the name is its first argument.
    if language == "elixir":
        return _elixir_module_parent(def_node, src)

    # Objective-C containers name themselves with a bare first identifier.
    if language == "objectivec":
        return _objc_container_parent(def_node, config.parent_class_types, src)

    # COBOL: a level-88 condition name is a value of the item before it.
    if language == "cobol":
        return cobol_condition_parent(def_node)
    return None


def _go_interface_owner(def_node: Node, src: str) -> str | None:
    """Name of the interface type_spec that directly declares def_node."""
    iface = def_node.parent
    if iface is None or iface.type != "interface_type":
        return None
    spec = iface.parent
    if spec is None or spec.type != "type_spec":
        return None
    name = spec.child_by_field_name("name")
    return _node_text(name, src).strip() if name is not None else None


def _find_parent(
    def_node: Node,
    config: LanguageConfig,
    receiver_nodes: list[Node],
    src: str,
) -> str | None:
    """Determine the parent class/type for a symbol."""
    if config.parent_extraction == "receiver":
        # Go interface methods belong to their named interface declaration.
        if def_node.type == "method_elem":
            return _go_interface_owner(def_node, src)
        # Go: extract type name from receiver parameter list
        if receiver_nodes:
            return extract_go_receiver_type(_node_text(receiver_nodes[0], src))
        return None

    if config.parent_extraction in ("nesting", "impl"):
        return _nesting_parent(def_node, config, src)

    return None  # "none" mode


def _finalize_symbols(
    symbols: list[Symbol], root: Node, src: str, file_info: FileInfo
) -> list[Symbol]:
    """Add generator-emitted symbols and settle colliding ids.

    Runs before calls and references are extracted, so both attribute to
    the final ids.
    """
    lang = file_info.language
    synthetic = extract_synthetic_symbols(root, src, file_info)
    if synthetic:
        # Keyed with the discriminator too, so a record's synthetic
        # canonical constructor survives beside an explicit overload of
        # another arity. A language without one dedupes on the id alone.
        existing = {(s.id, symbol_discriminator(s)) for s in symbols}
        symbols.extend(s for s in synthetic if (s.id, symbol_discriminator(s)) not in existing)
    if lang == "csharp":
        # The grammar reads a C# 14 extension block as a constructor
        # named ``extension``; its members were added above instead.
        symbols = drop_extension_block_symbols(symbols, root, src)
    # Anonymous members go last, once the overloads holding them carry
    # their discriminators.
    anonymous = [s for s in symbols if is_anonymous_member(s)]
    disambiguate_colliding_ids([s for s in symbols if not is_anonymous_member(s)], lang)
    follow_renamed_hosts(symbols)
    disambiguate_colliding_ids(anonymous, lang)
    return symbols

def _python_name_alias_chain(def_node: Node, src: str) -> tuple[list[str], str | None]:
    """Every bare-identifier target of a Python assignment, and its terminal name.

    ``a = b = c`` nests in tree-sitter-python: the outer assignment's own
    ``right`` field is itself an ``assignment`` node, not a sibling target, so
    walking ``right`` is what reaches ``b`` at all -- the query that captures
    ``@symbol.def`` only ever matches the outermost ``a``. The walk stops, and
    returns ``(targets_so_far, None)``, the moment a target is not a bare
    identifier (tuple unpacking, an attribute target) or the chain bottoms out
    on anything but a bare identifier (a call, a literal, an attribute access)
    -- those are not an alias of a name and must not be treated as one (#2791).

    Returns ``([], None)`` for a plain, non-chained ``x = <non-name>``, since
    there is nothing beyond the one target the caller already has.
    """
    targets: list[str] = []
    node: Node | None = def_node
    while node is not None and node.type == "assignment":
        left = node.child_by_field_name("left")
        if left is None or left.type != "identifier":
            return [], None
        targets.append(_node_text(left, src))
        node = node.child_by_field_name("right")
    if node is None or node.type != "identifier":
        return [], None
    return targets, _node_text(node, src)

def _python_assignment_alias_symbols(
    symbol: Symbol, def_node: Node, file_path: str, src: str
) -> list[Symbol]:
    """Mark *symbol* as an alias in place, and return the chain's other targets.

    ``symbol`` is already built for the outermost target (``s`` in
    ``s = attributes = widget``), the only one the query itself captures. This
    walks the rest of the chain and:

    - sets ``symbol.alias_of`` when the chain bottoms out on a bare name, so
      the call resolver can redirect calls through ``s`` onto ``widget``;
    - returns one freshly built ``Symbol`` per further target (``attributes``
      here), none of which the query ever sees at all today, each carrying
      the same ``alias_of``.

    A target equal to the terminal name (``s = s``) is left as an ordinary,
    non-aliased symbol -- there is no other definition to redirect onto.
    Returns ``[]``, and leaves ``symbol`` untouched, for every shape outside
    this issue's scope: no chain, a non-identifier target, or a right-hand
    side that is not a bare name (#2791).
    """
    targets, terminal = _python_name_alias_chain(def_node, src)
    if terminal is None or not targets:
        return []
    if targets[0] != terminal:
        symbol.alias_of = terminal

    extra: list[Symbol] = []
    for target in targets[1:]:
        extra.append(
            replace(
                symbol,
                id=f"{file_path}::{target}::alias@{def_node.start_byte}",
                name=target,
                qualified_name=_build_qualified_name(file_path, None, target),
                alias_of=terminal if target != terminal else None,
            )
        )
    return extra