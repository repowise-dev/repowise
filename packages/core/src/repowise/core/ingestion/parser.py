"""Unified AST parser — one class for all languages.

Architecture
============
Per-language differences live in two places:
  1. ``packages/core/queries/<lang>.scm``  — tree-sitter S-expression queries
     that capture symbols and imports using consistent capture-name conventions.
  2. ``LANGUAGE_CONFIGS`` dict in this module — a ``LanguageConfig`` per language
     that maps node types to symbol kinds, defines visibility rules, etc.

``ASTParser`` itself contains *no* if/elif language branches.  Adding support
for a new language means writing one ``.scm`` file and one ``LanguageConfig``
entry.  No Python class, no new module.

Capture-name conventions (shared across ALL .scm files):
  @symbol.def       — the full definition node (line numbers, kind lookup)
  @symbol.name      — name identifier
  @symbol.params    — parameter list (optional)
  @symbol.modifiers — decorators / visibility modifiers (optional)
  @symbol.receiver  — Go method receiver (optional, used for parent detection)
  @import.statement — full import node
  @import.module    — module path being imported
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import cache
from pathlib import Path

import structlog
from tree_sitter import Language, Node, Parser

from .cpp_export_macros import (
    CppExportTypes,
    _cpp_export_macro_parent,
    _cpp_normalize_identifier,
    _CppExportType,
    _is_bodiless_cpp_type,
    collect_cpp_export_types,
)
from .extractors import (
    build_signature,
    extract_go_receiver_type,
    extract_heritage,
    extract_import_bindings,
    extract_module_docstring,
    extract_symbol_docstring,
    node_text,
    refine_elixir_call_kind,
    refine_fsharp_type_kind,
    refine_go_type_kind,
    refine_kotlin_class_kind,
    refine_pascal_type_kind,
)
from .extractors.bindings.elixir import elixir_import_modules
from .extractors.bindings.python import expand_bare_relative_imports
from .extractors.bindings.ts_js import (
    declarator_binds_callable,
    declarator_value_is_module_ref,
)
from .extractors.synthetic_symbols import extract_synthetic_symbols
from .extractors.visibility import (
    refine_cpp_visibility,
    refine_csharp_visibility,
    refine_rust_visibility,
    refine_ts_visibility,
    ts_deferred_export_names,
    ts_export_aliases,
)
from .language_configs import LANGUAGE_CONFIGS, LanguageConfig
from .languages.registry import REGISTRY as _LANG_REGISTRY
from .models import (
    CallReceiver,
    CallSite,
    FileInfo,
    Import,
    ParsedFile,
    Symbol,
    TypeReference,
    compute_content_hash,
)
from .parser_helpers import (
    TYPE_HEAD_EXTRACTORS,
    _build_qualified_name,
    _classify_param_origin,
    _collect_error_nodes,
    _count_arguments,
    _dedupe_objc_interface_symbols,
    _dedupe_pascal_interface_symbols,
    _elixir_call_is_definitional,
    _elixir_is_template_definition,
    _elixir_module_parent,
    _elixir_symbol_name,
    _find_enclosing_symbol,
    _fsharp_binding_end_line,
    _fsharp_binding_has_params,
    _fsharp_binding_is_nested,
    _fsharp_parent_is_type,
    _fsharp_parent_name,
    _has_callable_ancestor,
    _head_type_identifier,
    _is_async_node,
    _objc_call_is_block_variable,
    _objc_container_node,
    _objc_container_parent,
    _objc_is_macro_enum,
    _objc_message_selector,
    _objc_symbol_name,
    _qualified_cpp_parent,
    _qualified_pascal_parent,
    _run_query,
    _rust_shadowed_by_type_param,
)
from .python_local_refs import extract_python_local_refs
from .sfc_source import component_call_sites, prepare_source
from .special_handlers import SPECIAL_HANDLER_LANGUAGES, parse_special

log = structlog.get_logger(__name__)

# Any single file emitting more than this many symbols is almost
# certainly machine-generated (large gRPC service contracts, OpenAPI
# bindings, SQL schema bindings). Warn rather than truncate — operators
# can decide whether to add the file to ``_NEVER_FLAG_PATTERNS`` or to
# exclude it via traversal.
_SYMBOL_COUNT_WARN_THRESHOLD = 500

QUERIES_DIR = Path(__file__).parent / "queries"

# Node types whose .scm patterns are anchored at module/program level
# (constants and module variables). They can never be function-local, so
# the callable-ancestor filter must not apply — and for TS/JS declarators
# it would misfire on the parent lexical_declaration kind mapping.
_MODULE_ANCHORED_NODE_TYPES = frozenset({"assignment", "variable_declarator"})

# Languages whose source reaches the parser as TypeScript/JavaScript. The two
# SFC tags are here because ``sfc_source`` projects their <script> blocks into
# a TS buffer at identical offsets, so every TS/JS code path applies verbatim.
_TS_JS_LANGUAGES = ("typescript", "javascript", "svelte", "vue")

# Languages whose query defines the ``@reference.*`` captures. Every other
# language would only scan the whole match list to find nothing, so the check
# is here rather than inside ``_extract_references``.
_REFERENCE_LANGUAGES = ("cpp", "c", "go", "rust", "kotlin")


def _call_receiver_from_node(node: Node, src: str) -> CallReceiver | None:
    """Describe an inner call captured as another call's receiver.

    The queries identify the complete inner AST node.  This helper reads only
    named tree-sitter fields, so nested arguments and formatting cannot change
    which call is carried into resolution.
    """

    arguments = node.child_by_field_name("arguments")
    argument_count = _count_arguments(arguments) if arguments is not None else None

    target = node.child_by_field_name("name")
    receiver = node.child_by_field_name("object")
    if target is None:
        function = node.child_by_field_name("function")
        if function is None:
            return None
        if function.type in ("identifier", "property_identifier", "field_identifier"):
            target = function
            receiver = None
        elif function.type == "generic_name":
            target = function.child_by_field_name("name") or next(
                (child for child in function.named_children if child.type == "identifier"), None
            )
            receiver = None
        else:
            target = (
                function.child_by_field_name("name")
                or function.child_by_field_name("property")
                or function.child_by_field_name("field")
            )
            receiver = (
                function.child_by_field_name("expression")
                or function.child_by_field_name("object")
                or function.child_by_field_name("argument")
            )

    if target is None:
        return None
    target_name = _node_text(target, src).strip()
    if not target_name:
        return None

    receiver_name = None
    if receiver is not None and receiver.type in ("identifier", "this"):
        receiver_name = _node_text(receiver, src).strip() or None
    return CallReceiver(target_name, receiver_name, argument_count)


@cache
def _compile_query(lang: str, grammar_tag: str | None = None) -> tuple[object | None, str | None]:
    """Compile and return (Query, None) or (None, error_str).

    Cached process-wide so preflight and parsing workers never recompile the same query.
    """
    grammar = grammar_tag or lang
    language = _get_language(grammar)
    if language is None:
        return None, None

    # The spec names the query file, so a language can reuse another's
    # queries wholesale (svelte -> typescript.scm). Every other spec declares
    # ``<tag>.scm``, which is what the default preserves.
    spec = _LANG_REGISTRY.get(lang)
    scm_name = (spec.scm_file if spec and spec.scm_file else None) or f"{lang}.scm"
    scm_path = QUERIES_DIR / scm_name
    if not scm_path.exists():
        log.debug("No .scm query file found", language=lang, path=str(scm_path))
        return None, None

    scm_text = scm_path.read_text(encoding="utf-8")
    # Grammar-variant-specific additions (e.g. JSX node captures that are
    # only valid against the ``tsx`` grammar but not the plain ``typescript``
    # one). Appended to the base SCM only when the variant scm file exists.
    if grammar_tag and grammar_tag != lang:
        extra_scm = QUERIES_DIR / f"{grammar_tag}.scm"
        if extra_scm.exists():
            scm_text = scm_text + "\n" + extra_scm.read_text(encoding="utf-8")
    try:
        from tree_sitter import Query  # type: ignore[attr-defined]

        return Query(language, scm_text), None
    except Exception as exc:
        return None, str(exc)


@cache
def _load_compiled_query(lang: str, grammar_tag: str | None = None) -> object | None:
    """Process-wide cache of compiled tree-sitter Query objects.

    Compiling `.scm` queries is non-trivial; in process-pool parsing each worker
    would otherwise recompile per file. ``grammar_tag`` may differ from
    ``lang`` when a language reuses another's grammar at a different
    variant — e.g. ``.tsx`` files reuse ``typescript.scm`` but must bind
    to the JSX-aware ``tsx`` grammar so React components don't drown in
    ERROR nodes.
    """
    query, err = _compile_query(lang, grammar_tag)
    if err is not None:
        log.warning("Failed to compile query", language=lang, error=err)
    return query


# Languages that intentionally have no AST parser.  Derived from the
# centralised LanguageRegistry — only non-code passthrough languages are
# included (not the extra git-blame-only languages).

# Excludes "openapi" (handled by special_handlers) and "unknown".
_PASSTHROUGH_LANGUAGES: frozenset[str] = _LANG_REGISTRY.unparseable_data_languages()

# ---------------------------------------------------------------------------
# Language registry — maps language tag → tree-sitter Language object
# ---------------------------------------------------------------------------


def _build_language_registry() -> dict[str, Language]:
    """Lazily load installed tree-sitter language packages.

    Driven by ``LanguageSpec.grammar_package`` / ``grammar_loader`` /
    ``shares_grammar_with`` from the centralised registry.
    """
    registry: dict[str, Language] = {}

    for spec in _LANG_REGISTRY.all_specs():
        # Languages that share another's grammar (e.g. C → cpp)
        if spec.shares_grammar_with:
            shared = registry.get(spec.shares_grammar_with)
            if shared:
                registry[spec.tag] = shared
            continue

        if not spec.grammar_package:
            continue

        try:
            mod = __import__(spec.grammar_package)
            loader_fn = getattr(mod, spec.grammar_loader)
            loaded = loader_fn(*spec.grammar_loader_args)
            # Standalone grammar wheels return a PyCapsule; shared grammar
            # packs may return the fully constructed Language directly.
            lang_obj = loaded if isinstance(loaded, Language) else Language(loaded)
            registry[spec.tag] = lang_obj
        except Exception as exc:
            log.debug(
                "tree-sitter language unavailable",
                language=spec.tag,
                reason=str(exc),
            )

    # TypeScript's tsx variant — special case: same package, different loader
    if "typescript" in registry and "tsx" not in registry:
        try:
            import tree_sitter_typescript as _ts_mod

            registry["tsx"] = Language(_ts_mod.language_tsx())
        except Exception as exc:
            log.debug("tree-sitter language unavailable", language="tsx", reason=str(exc))

    return registry


_LANGUAGE_REGISTRY: dict[str, Language] = {}

# Languages already reported as having a config but no installed grammar, so
# the report is one line per language per process instead of one per file. A
# repo with a few thousand shell scripts otherwise emitted a few thousand
# identical lines, all saying the same three facts.
_MISSING_GRAMMAR_REPORTED: set[str] = set()


def missing_grammar_languages(language_tags: Iterable[str]) -> list[str]:
    """Of *language_tags*, those that parse via tree-sitter but have no grammar.

    Answers the question once, in the parent, before up to eight spawned
    workers each rediscover it and log their own copy of the answer at a level
    the CLI discards anyway.

    Deliberately uses ``find_spec`` rather than importing: the whole point is
    to stay cheap enough to run on every index. Building the real registry here
    would import every tree-sitter package into the parent process, which is
    memory the parse pool is about to need for something else.

    A tag with no :data:`LANGUAGE_CONFIGS` entry is not a gap — nothing claims
    to parse it — so it is skipped rather than reported.

    Ceiling: "importable" is not "loadable". A grammar whose compiled ABI does
    not match the installed ``tree_sitter`` imports fine and then raises inside
    ``Language()``, which this cannot see, so that case reports nothing here
    and stays a per-worker debug line. Reporting it properly means loading the
    grammars, which is the cost this function exists to avoid.
    """
    import importlib.util

    specs = {spec.tag: spec for spec in _LANG_REGISTRY.all_specs()}
    missing: list[str] = []
    for tag in language_tags:
        if tag not in LANGUAGE_CONFIGS:
            continue
        spec = specs.get(tag)
        if spec is None:
            continue
        package = spec.grammar_package
        if not package and spec.shares_grammar_with:
            shared = specs.get(spec.shares_grammar_with)
            package = shared.grammar_package if shared else None
        if not package:
            continue
        try:
            if importlib.util.find_spec(package) is None:
                missing.append(tag)
        except (ImportError, ValueError):
            missing.append(tag)
    return sorted(missing)


def failed_query_languages(language_tags: Iterable[str]) -> list[tuple[str, str]]:
    """Of *language_tags*, those whose tree-sitter queries fail to compile.

    Scoped to what traversal discovered in the repo. Only checks languages with
    a registered LanguageConfig and query file. Best-effort and bounded.
    """
    failed: list[tuple[str, str]] = []
    seen: set[str] = set()

    for tag in language_tags:
        if tag in seen or tag not in LANGUAGE_CONFIGS:
            continue
        seen.add(tag)

        _, err = _compile_query(tag)
        if err is not None:
            failed.append((tag, err))
            continue

        # Check grammar variants if applicable (e.g. tsx for typescript)
        if tag == "typescript":
            _, tsx_err = _compile_query("typescript", grammar_tag="tsx")
            if tsx_err is not None:
                failed.append((tag, f"tsx variant: {tsx_err}"))

    return sorted(failed, key=lambda x: x[0])


def _get_language(tag: str) -> Language | None:
    global _LANGUAGE_REGISTRY
    if not _LANGUAGE_REGISTRY:
        _LANGUAGE_REGISTRY = _build_language_registry()
    return _LANGUAGE_REGISTRY.get(tag)


def grammar_tag_for(language: str, path: str) -> str:
    """The grammar a file is read with, which is not always its language tag.

    A ``.tsx`` file arrives tagged ``typescript``, and tree-sitter-typescript's
    default grammar errors on every ``<Component />``. Only the grammar moves:
    the language tag keeps selecting dialects and vocabularies, several of
    which (``mocks/lexicon.py`` among them) carry no ``tsx`` row and would
    silently degrade if handed one.
    """
    # Case-folded, because the extension table that tagged the file is.
    if language == "typescript" and path.lower().endswith(".tsx"):
        return "tsx"
    return language


# Private alias for internal use (kept for compatibility with _find_parent)
_node_text = node_text


def _normalize_php_receiver(text: str) -> str:
    """Spell PHP's receiver the way the resolver's strategies expect.

    `self::` and `static::` are the same dispatch as `$this`, and the self/this
    strategy tests `in ("self", "this")` — so without this the whole implicit-
    receiver population misses. `parent::` needs the heritage walk and is left
    to miss rather than guessed at.
    """
    name = text.lstrip("$")
    return "this" if name in ("self", "static") else name


# ---------------------------------------------------------------------------
# ASTParser
# ---------------------------------------------------------------------------


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

    # Refine "class" kind for Kotlin (interface / enum class share class_declaration)
    if kind == "class" and language == "kotlin" and node_type == "class_declaration":
        kind = refine_kotlin_class_kind(def_node)

    # Refine "class" kind for Pascal (declType wraps class / record /
    # object / interface / class-helper / enum / set / array / alias
    # in one node shape -- see the spec docstring and
    # refine_pascal_type_kind's own docstring for the disambiguation).
    if kind == "class" and language == "pascal" and node_type == "declType":
        kind = refine_pascal_type_kind(def_node)

    # Elixir: every definition is a ``call``, so the node type cannot
    # name the kind and the config maps it to a deliberately
    # non-callable placeholder (see refine_elixir_call_kind). The
    # keyword in the call's target is what actually says what was
    # defined.
    if language == "elixir" and node_type == "call":
        kind = refine_elixir_call_kind(def_node, src)

    # F# writes a class, a struct and an interface with the same
    # ``anon_type_defn`` node; only the body says which it is.
    if kind == "class" and language == "fsharp" and node_type == "anon_type_defn":
        kind = refine_fsharp_type_kind(def_node)
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
    # F#: the captured node is the binding's left-hand side, so its
    # own span stops at the parameter list. Extend it over the body
    # (and any return-type annotation between the two) or every call
    # in the body is attributed to whatever encloses the binding.
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
    # TS/JS: the symbol query admits call_expression values so
    # forwardRef / memo / onCall / styled() bindings exist at all,
    # which also lets `const svc = require('./svc')` through. Those
    # bind a module and are already imports — drop them here rather
    # than in the query, which cannot see past the await / paren /
    # non-null / member-pick shells.
    if language in _TS_JS_LANGUAGES and declarator_value_is_module_ref(def_node, src):
        return None
    # A declarator whose value is structurally callable is not
    # data, whatever its name looks like: `const C =
    # forwardRef(fn)` and `const f = function(){}` are a component
    # and a function. Naming decides only for the rest, which is
    # what it was ever able to answer.
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
    # C#: [Obsolete] / [System.Obsolete] are ``attribute_list`` nodes.
    # In tree-sitter-c-sharp the attribute_list is child[0] of the
    # declaration node itself (method_declaration, class_declaration, etc.),
    # NOT a preceding sibling in the class body.
    if language == "csharp":
        return _leading_child_attributes(def_node, "attribute_list", "[", "]", src)
    # C/C++: [[deprecated]] / [[deprecated("reason")]] are
    # ``attribute_declaration`` nodes, child[0] of function_definition itself
    # (NOT a preceding sibling at translation_unit level).
    if language in ("cpp", "c"):
        return _leading_child_attributes(def_node, "attribute_declaration", "[[", "]]", src)
    return []


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
    """Inner text of the attribute children leading *def_node*, until the first other child."""
    attrs: list[str] = []
    for child in def_node.children:
        if child.type != node_type:
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
    src: str,
) -> tuple[str, bool]:
    """``(visibility, is_exported_symbol)`` after the language's AST-context rules."""
    # C/C++ visibility is dictated by AST context (access
    # specifiers / storage class / export attributes), not by
    # modifier text. Refine after the generic fn ran.
    if language in ("cpp", "c"):
        return refine_cpp_visibility(def_node, visibility, src)
    # C#: an unmodified declaration's default depends on what encloses
    # it, which the modifier-text fn cannot see.
    if language == "csharp":
        return refine_csharp_visibility(def_node, visibility), False
    # TS/JS: a top-level declaration is only public when exported —
    # inline, via ``export { x }`` lists, or ``export default x``.
    if language in _TS_JS_LANGUAGES:
        return refine_ts_visibility(def_node, visibility, name, ts_deferred_exports), False
    # Rust: a trait's items may not write ``pub`` of their own, so the
    # trait's modifier is the only place their visibility is stated.
    if language == "rust":
        return refine_rust_visibility(def_node, visibility, src), False
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


def _pascal_imports(
    stmt_node: Node, module_node: Node, src: str, seen_units: set[str]
) -> list[Import]:
    """One Pascal ``uses`` unit, deduped case-insensitively across the file.

    pascal.scm fires once per unit name, so a 3-unit clause arrives as 3
    matches sharing one statement span: deduping by raw text would drop all
    but the first. A unit named in both ``uses`` clauses is one dependency.
    ``uses`` exposes the unit's whole interface, hence the ``*`` wildcard.
    """
    raw = _node_text(stmt_node, src).strip()
    unit_name = _node_text(module_node, src).strip()
    if not unit_name or unit_name.lower() in seen_units:
        return []
    seen_units.add(unit_name.lower())
    return [
        Import(
            raw_statement=raw,
            module_path=unit_name,
            imported_names=["*"],
            is_relative=False,
            resolved_file=None,
            bindings=[],
            is_reexport=False,
        )
    ]


def _elixir_imports(
    stmt_node: Node, module_node: Node, src: str, seen_modules: set[str]
) -> list[Import]:
    """Elixir directives, one Import per module and deduped by module path.

    ``alias Foo.{Bar, Baz}`` names two modules in one statement. ``import``
    pulls in every public function (``*``); alias/require/use bind the module.
    """
    raw = _node_text(stmt_node, src).split("\n", 1)[0].strip()
    directive_node = stmt_node.child_by_field_name("target")
    directive = _node_text(directive_node, src).strip() if directive_node else ""
    imports: list[Import] = []
    for module_path in elixir_import_modules(module_node, src):
        if module_path in seen_modules:
            continue
        seen_modules.add(module_path)
        imports.append(
            Import(
                raw_statement=raw,
                module_path=module_path,
                imported_names=["*"] if directive == "import" else [],
                is_relative=False,
                resolved_file=None,
                bindings=[],
                is_reexport=False,
            )
        )
    return imports


def _fsharp_imports(
    stmt_node: Node, module_node: Node, src: str, seen_raws: set[str]
) -> list[Import]:
    """F# ``open``: every public name of the module (``*``), which bare-name
    call resolution reads. ``open type A.B.T`` binds the type's static members,
    so the module the file depends on is ``A.B``.
    """
    raw = _node_text(stmt_node, src).strip()
    if raw in seen_raws:
        return []
    seen_raws.add(raw)
    module_path = _node_text(module_node, src).strip()
    if not module_path:
        return []
    names: list[str] = ["*"]
    if any(child.type == "type" for child in stmt_node.children):
        head, _, type_name = module_path.rpartition(".")
        if head:
            module_path, names = head, [type_name]
        else:
            names = []
    return [
        Import(
            raw_statement=raw,
            module_path=module_path,
            imported_names=names,
            is_relative=False,
            resolved_file=None,
            bindings=[],
            is_reexport=False,
        )
    ]


def _statement_imports(
    stmt_node: Node,
    module_node: Node,
    module_text: str,
    raw: str,
    language: str,
    src: str,
) -> list[Import]:
    """The imports one deduplicated statement declares."""
    if language == "scala" and stmt_node.type == "import_declaration":
        return _scala_imports(stmt_node, raw, src)
    if language == "php" and stmt_node.type == "namespace_use_declaration":
        return _php_imports(stmt_node, raw, src)
    if language == "dart":
        return [_dart_import(stmt_node, module_node, module_text, raw, src)]
    if language in _TS_JS_LANGUAGES and _is_dynamic_esm_import(stmt_node):
        # ``import('./mod')`` binds a module namespace at runtime, so it is a
        # wildcard, which keeps the target's exports live.
        return [
            Import(
                raw_statement=raw,
                module_path=module_text,
                imported_names=["*"],
                is_relative=module_text.startswith("."),
                resolved_file=None,
                bindings=[],
                is_reexport=False,
            )
        ]
    if language in ("javascript", "typescript") and stmt_node.type in (
        "assignment_expression",
        "call_expression",
    ):
        return _cjs_imports(stmt_node, raw, src)
    return [_generic_import(stmt_node, module_text, raw, language, src)]


def _scala_imports(stmt_node: Node, raw: str, src: str) -> list[Import]:
    """Scala: one Import per selected name, with full dotted paths.

    The query's ``(identifier)`` capture is only the first path segment, and
    one declaration can hold several clauses, brace selectors, renames and
    wildcards.
    """
    from .extractors.bindings.scala import expand_scala_import_clauses
    from .models import NamedBinding

    imports: list[Import] = []
    for clause_path, clause_names in expand_scala_import_clauses(stmt_node, src):
        local = clause_names[0]
        exported = None if local == "*" else clause_path.rsplit(".", 1)[-1]
        imports.append(
            Import(
                raw_statement=raw,
                module_path=clause_path,
                imported_names=clause_names,
                is_relative=False,
                resolved_file=None,
                bindings=[
                    NamedBinding(
                        local_name=local,
                        exported_name=exported,
                        source_file=None,
                    )
                ],
                is_reexport=False,
            )
        )
    return imports


def _php_imports(stmt_node: Node, raw: str, src: str) -> list[Import]:
    """PHP: one ``use`` declaration can name several classes, each its own file."""
    from .extractors.bindings.php import php_use_clauses
    from .models import NamedBinding

    return [
        Import(
            raw_statement=raw,
            module_path=fqn,
            imported_names=[local],
            is_relative=False,
            resolved_file=None,
            bindings=[NamedBinding(local_name=local, exported_name=fqn, source_file=None)],
            is_reexport=False,
        )
        for fqn, local in php_use_clauses(stmt_node, src)
    ]


def _dart_import(
    stmt_node: Node, module_node: Node, module_text: str, raw: str, src: str
) -> Import:
    """Dart: URIs are relative unless schemed, ``export`` re-exports, and the
    dotted ``part of library.name;`` form resolves through ``library:``.
    """
    module_path = module_text
    if module_node.type == "dotted_identifier_list":
        module_path = f"library:{module_text}"
    imported_names, bindings = extract_import_bindings(stmt_node, src, "dart")
    return Import(
        raw_statement=raw,
        module_path=module_path,
        imported_names=imported_names,
        is_relative=not module_path.startswith(("package:", "dart:", "library:")),
        resolved_file=None,
        bindings=bindings,
        is_reexport=stmt_node.type == "library_export",
    )


def _is_dynamic_esm_import(stmt_node: Node) -> bool:
    if stmt_node.type != "call_expression":
        return False
    fn = stmt_node.child_by_field_name("function")
    return fn is not None and fn.type == "import"


def _cjs_imports(stmt_node: Node, raw: str, src: str) -> list[Import]:
    """CommonJS: every ``require()`` in the statement, re-exports marked.

    A hub like ``Object.assign(module.exports, require('./a'), ...)`` is
    several imports, and ``module.exports`` shapes are barrels.
    """
    from .extractors.bindings.ts_js import (
        cjs_statement_is_reexport,
        collect_cjs_requires,
    )

    cjs_reexport = cjs_statement_is_reexport(stmt_node, src)
    return [
        Import(
            raw_statement=raw,
            module_path=cjs_module,
            imported_names=["*"] if cjs_reexport else [],
            is_relative=cjs_module.startswith("."),
            resolved_file=None,
            bindings=[],
            is_reexport=cjs_reexport,
        )
        for cjs_module in collect_cjs_requires(stmt_node, src)
    ]


def _generic_import(
    stmt_node: Node, module_text: str, raw: str, language: str, src: str
) -> Import:
    """The single Import of a statement no language-specific shape claims."""
    if language == "rust" and stmt_node.type == "mod_item":
        module_text = _rust_mod_path_attribute(stmt_node, src) or module_text

    # JVM wildcard imports: the query captures the scoped identifier only,
    # so ``import com.foo.*`` arrives as ``com.foo``. Restore the ``.*``.
    if language in ("java", "kotlin") and not module_text.endswith("*"):
        stmt_text = raw.rstrip().rstrip(";").rstrip()
        if stmt_text.endswith(".*"):
            module_text += ".*"

    # Language-specific import name + binding extraction
    imported_names, bindings = extract_import_bindings(stmt_node, src, language)
    is_relative = (
        module_text.startswith(".")
        or module_text.startswith("./")
        or module_text.startswith(("self::", "super::", "crate::"))
    )
    return Import(
        raw_statement=raw,
        module_path=module_text,
        imported_names=imported_names,
        is_relative=is_relative,
        resolved_file=None,
        bindings=bindings,
        is_reexport=_is_reexport_import(stmt_node, raw, language),
    )


def _rust_mod_path_attribute(stmt_node: Node, src: str) -> str | None:
    """The ``#[path = "..."]`` override on a Rust ``mod`` item, if any.

    Outer attributes are preceding siblings of the item, not children.
    """
    parent = stmt_node.parent
    if parent is None:
        return None
    siblings = parent.children
    for j, sib in enumerate(siblings):
        if sib.id != stmt_node.id:
            continue
        # Walk backward through preceding attribute_item siblings
        k = j - 1
        while k >= 0 and siblings[k].type == "attribute_item":
            path_match = re.search(r'path\s*=\s*"([^"]+)"', _node_text(siblings[k], src))
            if path_match:
                return path_match.group(1)
            k -= 1
        return None
    return None


def _is_reexport_import(stmt_node: Node, raw: str, language: str) -> bool:
    """``pub use`` (Rust) and ``@_exported import`` (Swift) re-export the module."""
    if language == "rust" and stmt_node.type == "use_declaration":
        return any(child.type == "visibility_modifier" for child in stmt_node.children)
    return language == "swift" and raw.startswith("@_exported")


class ASTParser:
    """Unified AST parser — works for all languages via .scm query files.

    Usage::

        parser = ASTParser()
        parsed = parser.parse_file(file_info, source_bytes)

    Adding a new language:
    1. Write ``packages/core/queries/<lang>.scm``
    2. Add one entry to ``LANGUAGE_CONFIGS``
    That's it.  No Python class, no new module.
    """

    def __init__(self) -> None:
        pass

    def parse_file(self, file_info: FileInfo, source: bytes) -> ParsedFile:
        """Parse *source* bytes and return a fully populated ParsedFile."""
        lang = file_info.language
        content_hash = compute_content_hash(source)

        # Non-tree-sitter formats (OpenAPI, Dockerfile, Makefile, SQL) parse
        # via dedicated handlers. Checked before the grammar lookup: none of
        # these tags carry a LanguageConfig, so the no-grammar fallback below
        # would otherwise swallow them.
        if lang in SPECIAL_HANDLER_LANGUAGES:
            parsed = parse_special(file_info, source, lang)
            parsed.content_hash = content_hash
            return parsed

        config = LANGUAGE_CONFIGS.get(lang)
        # .tsx needs the JSX-aware grammar: the default one's ERROR-node
        # recovery hoists nested helpers (handlers defined inside component
        # bodies) to the top level.
        grammar_tag = grammar_tag_for(lang, file_info.path)
        language = _get_language(grammar_tag)

        # tree-sitter-fsharp ships a second grammar (``language_signature``)
        # for .fsi signature files, and a spec loads exactly one. Read with
        # the implementation grammar, a signature file's ``val`` and member
        # signatures land in ERROR recovery, which hoists whatever the
        # recovery invents into the symbol list. The regex tier still gives
        # these files their ``open`` imports, which is all a signature file
        # contributes that another file does not also state.
        signature_file = lang == "fsharp" and file_info.path.endswith(".fsi")

        if config is None or language is None or signature_file:
            if config is not None and language is None and lang not in _MISSING_GRAMMAR_REPORTED:
                # Once per language, not once per file: the fact is about the
                # environment, and it does not become truer on the four
                # thousandth shell script.
                _MISSING_GRAMMAR_REPORTED.add(lang)
                log.debug("tree-sitter grammar unavailable", language=lang)
            # Languages without a grammar may still carry regex-tier import
            # extraction (their specs declare import_support="partial");
            # symbols stay empty — the regex tier claims no symbol knowledge.
            from .lightweight_imports import extract_lightweight_imports

            return ParsedFile(
                file_info=file_info,
                symbols=[],
                imports=extract_lightweight_imports(file_info, source),
                exports=[],
                docstring=None,
                parse_errors=[],
                content_hash=content_hash,
            )

        # An SFC (.svelte, .vue) is three languages in one file.
        # ``prepare_source`` blanks the markup and <style> so what reaches the
        # TypeScript grammar is valid TS at byte-identical offsets — no offset
        # translation is needed anywhere downstream. A no-op for every other
        # language without a registered locator/sanitizer -- Pascal's
        # sanitizers (project-file `in '...'` clauses, ERROR-node blanking)
        # live behind the same hook; see prepare_pascal_source in
        # parser_helpers.py for why they're wired in here rather than as
        # ad-hoc if-blocks.
        # ``content_hash`` above deliberately hashes the ORIGINAL bytes, so
        # incremental update still tracks the real file.
        original_source = source
        source = prepare_source(lang, source, path=file_info.path)

        parser = Parser(language)
        tree = parser.parse(source)
        src = source.decode("utf-8", errors="replace")
        root = tree.root_node

        parse_errors = _collect_error_nodes(root)

        # Adaptive TSX grammar fallback: if a .ts file contains JSX markup,
        # tree-sitter-typescript produces ERROR nodes. Re-parse using the TSX
        # grammar ONLY IF:
        #   1. Initial parse yielded error nodes (parse_errors is non-empty)
        #   2. The file projects to TypeScript and is not already on tsx
        #   3. Source contains JSX-specific closing tokens (b"/>" or b"</")
        # A .vue render function may be written in JSX
        # (``vnodes.push(<i class={c} />)``), which is the single TS parse
        # failure across a 1,593-file .vue corpus. ``source`` here is the
        # markup-blanked projection, so the template's own ``</`` and ``/>``
        # are already spaces — the token test still keys on real JSX only.
        # The swap is strictly safe: it only takes effect when TSX yields
        # FEWER errors than the first parse.
        if (
            parse_errors
            and lang in ("typescript", "vue")
            and grammar_tag != "tsx"
            and (b"/>" in source or b"</" in source)
        ):
            tsx_language = _get_language("tsx")
            if tsx_language is not None:
                tsx_tree = Parser(tsx_language).parse(source)
                tsx_errors = _collect_error_nodes(tsx_tree.root_node)
                if len(tsx_errors) < len(parse_errors):
                    tree = tsx_tree
                    root = tree.root_node
                    parse_errors = tsx_errors
                    # Both grammar_tag AND language must be reassigned.
                    # grammar_tag is consumed immediately below by
                    # self._get_query(lang, language, grammar_tag), which
                    # appends tsx.scm to the base typescript.scm query.
                    # That append is what supplies the
                    # jsx_opening_element / jsx_self_closing_element captures
                    # that restore JSX component call-site edges.
                    # Reassigning only ``tree`` / ``root`` would fix parse
                    # errors but leave those edges missing — the dead-code
                    # false-positive would remain.
                    grammar_tag = "tsx"
                    language = tsx_language

        query = self._get_query(lang, language, grammar_tag)

        # Execute the compiled query ONCE per file. The five extraction
        # passes below all consume the same capture dicts read-only;
        # re-running ``cursor.matches()`` per pass multiplied the most
        # expensive part of parsing by five.
        matches = _run_query(query, root) if query is not None else []

        symbols = self._extract_symbols(matches, config, file_info, src)
        # Per-language synthetic-symbol pass — recognises source-generator
        # attributes (e.g. CommunityToolkit.Mvvm) and adds the symbols the
        # generator would emit at compile time. No-op for languages
        # without a registered extractor.
        synthetic = extract_synthetic_symbols(root, src, file_info)
        if synthetic:
            existing_ids = {s.id for s in symbols}
            symbols.extend(s for s in synthetic if s.id not in existing_ids)
        imports = self._extract_imports(matches, config, file_info, src)
        calls = self._extract_calls(matches, config, file_info, src, symbols)
        # An SFC instantiates a component by writing its tag in the markup
        # (``<Foo />``), which the blanked TS buffer no longer contains. Mint
        # those call sites from the markup grammar directly — the same way
        # tsx.scm turns ``<Component />`` into a call for React. Returns [] for
        # every non-SFC language.
        calls.extend(component_call_sites(lang, original_source, symbols))
        references = (
            self._extract_references(matches, file_info, src, symbols)
            if lang in _REFERENCE_LANGUAGES
            else []
        )
        heritage = extract_heritage(matches, config, file_info, src)
        exports = self._derive_exports(symbols, config)
        export_aliases = ts_export_aliases(src) if lang in _TS_JS_LANGUAGES else {}
        docstring = extract_module_docstring(root, src, lang)
        type_refs = self._extract_type_refs(matches, src, lang)

        # Same-file reference rescue (Python only): top-level symbols used
        # elsewhere in their own module in a non-call / non-import position
        # (callable passed as an arg, type annotation, decorator, default)
        # carry no graph edge, so the dead-code unused-export pass would flag
        # them. Stamp the referenced names so the analyzer can rescue them.
        local_refs: frozenset[str] = frozenset()
        if lang == "python":
            top_level_names = {s.name for s in symbols if s.name and not s.parent_name}
            local_refs = extract_python_local_refs(src, top_level_names)

        if len(symbols) > _SYMBOL_COUNT_WARN_THRESHOLD:
            log.warning(
                "parser.symbol_bloat",
                path=file_info.path,
                language=lang,
                symbol_count=len(symbols),
                threshold=_SYMBOL_COUNT_WARN_THRESHOLD,
            )

        return ParsedFile(
            file_info=file_info,
            symbols=symbols,
            imports=imports,
            exports=exports,
            export_aliases=export_aliases,
            calls=calls,
            heritage=heritage,
            docstring=docstring,
            parse_errors=parse_errors,
            content_hash=content_hash,
            type_refs=type_refs,
            local_refs=local_refs,
            references=references,
        )

    # ------------------------------------------------------------------
    # Query loading
    # ------------------------------------------------------------------

    def _get_query(
        self, lang: str, language: Language, grammar_tag: str | None = None
    ) -> object | None:
        """Load and cache the compiled tree-sitter Query for *lang*."""
        return _load_compiled_query(lang, grammar_tag)

    # ------------------------------------------------------------------
    # Symbol extraction
    # ------------------------------------------------------------------

    def _extract_symbols(
        self,
        matches: list[dict],
        config: LanguageConfig,
        file_info: FileInfo,
        src: str,
    ) -> list[Symbol]:
        language = file_info.language
        symbols: list[Symbol] = []
        seen: set[tuple[int, str]] = set()  # (start_line, name) — dedup decorated dupes
        # Parallel to ``symbols`` (same indices) -- only populated/consumed
        # for Pascal and Objective-C, to dedupe interface-declaration vs.
        # implementation method pairs after the loop. See
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
        cpp_exports = (
            collect_cpp_export_types(matches, src) if language == "cpp" else CppExportTypes()
        )

        for capture_dict in matches:
            built = self._symbol_from_match(
                capture_dict, config, file_info, src, cpp_exports, ts_deferred_exports, seen
            )
            if built is None:
                continue
            symbol, def_node = built
            symbols.append(symbol)
            node_types.append(def_node.type)
            if language == "objectivec":
                container = _objc_container_node(def_node, config.parent_class_types)
                objc_container_kinds.append(container.type if container else None)

        if language == "pascal":
            symbols = _dedupe_pascal_interface_symbols(symbols, node_types)

        # A .m file routinely declares its private methods in a class
        # extension and defines them below in the @implementation, which
        # builds each symbol id twice in one file.
        if language == "objectivec":
            symbols = _dedupe_objc_interface_symbols(symbols, node_types, objc_container_kinds)

        return symbols

    def _symbol_from_match(
        self,
        capture_dict: dict,
        config: LanguageConfig,
        file_info: FileInfo,
        src: str,
        cpp_exports: CppExportTypes,
        ts_deferred_exports: frozenset[str] | None,
        seen: set[tuple[int, str]],
    ) -> tuple[Symbol, Node] | None:
        """One query match as a symbol and its definition node, or None to drop it.

        Records the match's ``(start_line, name)`` in *seen* once it passes the
        name checks, so a later duplicate is dropped even if this one is.
        """
        language = file_info.language
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
            # Body-form macros are suppressed by name as before #1901;
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

        node_type = def_node.type
        kind = self._symbol_kind(def_node, config, language, src, cpp_exports.parent_ids)
        if kind is None:
            return None

        end_line = _symbol_end_line(def_node, config, language, export_type)
        if end_line is None:
            return None

        if node_type in _MODULE_ANCHORED_NODE_TYPES:
            kind = _module_binding_kind(def_node, name, language, src)
            if kind is None:
                return None

        modifier_texts = [_node_text(m, src) for m in capture_dict.get("symbol.modifiers", [])]
        visibility, is_exported_symbol = _refine_visibility(
            def_node,
            language,
            config.visibility_fn(name, modifier_texts),
            name,
            ts_deferred_exports,
            src,
        )

        parent_name = self._resolve_parent_name(
            def_node,
            config,
            capture_dict.get("symbol.receiver", []),
            name_nodes,
            language,
            src,
            no_export_type=export_type is None,
            export_type_parents=cpp_exports.parents,
        )

        # A ``field_declaration`` cannot occur outside a class body, so a
        # missing parent means the class did not parse. Grammar recovery,
        # not a member function.
        if node_type == "function_declarator" and parent_name is None:
            return None

        # Upgrade function → method when a parent class is detected.
        # F#: a nested module is a parent too (for id uniqueness), but it
        # is not a type, so a `let` inside one stays a function.
        if parent_name and kind == "function" and (
            language != "fsharp" or _fsharp_parent_is_type(def_node)
        ):
            kind = "method"

        params_nodes = capture_dict.get("symbol.params", [])
        params_text = _node_text(params_nodes[0], src) if params_nodes else ""
        sym_id = (
            f"{file_info.path}::{parent_name}::{name}"
            if parent_name
            else f"{file_info.path}::{name}"
        )
        symbol = Symbol(
            id=sym_id,
            name=name,
            qualified_name=_build_qualified_name(file_info.path, parent_name, name),
            kind=kind,  # type: ignore[arg-type]
            signature=build_signature(node_type, name, params_text, def_node, src),
            start_line=start_line,
            end_line=end_line,
            docstring=extract_symbol_docstring(def_node, src, language),
            decorators=(
                [m for m in modifier_texts if m.startswith("@")]
                + _attribute_decorators(def_node, language, src)
            ),
            visibility=visibility,  # type: ignore[arg-type]
            is_async=_is_async_node(def_node, src),
            language=language,
            parent_name=parent_name,
            is_exported_symbol=is_exported_symbol,
            is_declaration=(
                node_type in config.declaration_node_types
                or (export_type is not None and export_type.is_forward_declaration)
                or (
                    export_type is None
                    and _is_bodiless_cpp_type(language, node_type, def_node)
                )
            ),
        )
        return symbol, def_node

    def _symbol_kind(
        self,
        def_node: Node,
        config: LanguageConfig,
        language: str,
        src: str,
        export_type_parent_ids: frozenset[int],
    ) -> str | None:
        """The symbol kind for *def_node*, or None when it is not a symbol here."""
        node_type = def_node.type
        kind = config.symbol_node_types.get(node_type)
        if kind is None:
            return None

        # Skip symbols nested inside another function/method body. The
        # Tree-sitter query is recursive, so helpers defined inside a
        # React component or an async orchestrator method get hoisted
        # to the top-level symbol list and read as unused public
        # exports. Filtering by callable ancestor restricts extraction
        # to module-top-level + class-body members. Class bodies don't
        # match (``class_definition`` is not callable), so methods are
        # preserved. Module-anchored node types skip the check: their
        # .scm patterns only match at module/program level, and a TS
        # variable_declarator's parent (lexical_declaration → "function")
        # would otherwise read as a callable ancestor.
        if node_type not in _MODULE_ANCHORED_NODE_TYPES and _has_callable_ancestor(
            def_node, config.symbol_node_types, export_type_parent_ids
        ):
            return None

        # F#: a ``let`` nested in another binding's body has the same node
        # shape as a top-level one, so the ancestor filter above cannot
        # see it -- what it captures is the binding's left-hand side, and
        # a nested binding's left-hand side has no callable ancestor
        # either. A ``let`` inside a type body is a field and stays.
        if _is_fsharp_binding(language, node_type) and _fsharp_binding_is_nested(def_node):
            return None
        return _refine_symbol_kind(kind, def_node, config, language, src)

    def _resolve_parent_name(
        self,
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
        parent_name = self._find_parent(def_node, config, receiver_nodes, src)
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

        # C/C++ qualified definitions: ``void Foo::method() { … }``
        # carries the class as the scope of a ``qualified_identifier``
        # parent of the name node. Without this resolution, every
        # ``Class::method`` lands as a free function and bloats the
        # unused_export pass with thousands of method symbols.
        if language in ("cpp", "c") and name_nodes:
            return _qualified_cpp_parent(name_nodes[0], src)

        # Pascal out-of-line implementation: ``function TFoo.Bar(...);``
        # -- the ``defProc`` node lives in the unit's implementation
        # section, outside the class's ``declType`` body declared in the
        # interface section, so nesting-based ``_find_parent`` above
        # can't see it. The qualifying class lives beside the captured
        # name in the ``genericDot`` header instead.
        if language == "pascal" and name_nodes:
            return _qualified_pascal_parent(name_nodes[0], src)

        # Elixir: the enclosing ``defmodule`` is a ``call`` with no
        # ``name`` field for the generic nesting walk to read, so the
        # module name has to be dug out of its first argument.
        if language == "elixir":
            return _elixir_module_parent(def_node, src)

        # Objective-C: an @interface / @implementation / @protocol names
        # itself with a bare first identifier and no ``name`` field, so
        # the nesting walk above finds the right ancestor and reads
        # nothing off it.
        if language == "objectivec":
            return _objc_container_parent(def_node, config.parent_class_types, src)
        return None

    def _find_parent(
        self,
        def_node: Node,
        config: LanguageConfig,
        receiver_nodes: list[Node],
        src: str,
    ) -> str | None:
        """Determine the parent class/type for a symbol."""
        if config.parent_extraction == "receiver":
            # Go: extract type name from receiver parameter list
            if receiver_nodes:
                return extract_go_receiver_type(_node_text(receiver_nodes[0], src))
            return None

        if config.parent_extraction in ("nesting", "impl"):
            # Walk up the AST to find a class/impl ancestor
            ancestor = def_node.parent
            while ancestor is not None:
                if ancestor.type in config.parent_class_types:
                    name_node = ancestor.child_by_field_name("name") or (
                        ancestor.child_by_field_name("type")  # Rust impl_item
                    )
                    if name_node:
                        # For Rust impl blocks with generic types (e.g. impl<T> Foo<T>),
                        # extract only the base type name, not the full generic signature.
                        if name_node.type == "generic_type":
                            inner = name_node.child_by_field_name("type")
                            if inner and inner.type == "type_identifier":
                                name_node = inner
                        elif name_node.type == "scoped_type_identifier":
                            inner = name_node.child_by_field_name("name")
                            if inner and inner.type == "type_identifier":
                                name_node = inner
                        return _node_text(name_node, src)
                ancestor = ancestor.parent
            return None

        return None  # "none" mode

    # ------------------------------------------------------------------
    # Import extraction
    # ------------------------------------------------------------------

    def _extract_imports(
        self,
        matches: list[dict],
        config: LanguageConfig,
        file_info: FileInfo,
        src: str,
    ) -> list[Import]:
        language = file_info.language
        imports: list[Import] = []
        seen_raws: set[str] = set()
        seen_pascal_units: set[str] = set()
        seen_elixir_modules: set[str] = set()

        for capture_dict in matches:
            stmt_nodes = capture_dict.get("import.statement", [])
            module_nodes = capture_dict.get("import.module", [])

            if not stmt_nodes or not module_nodes:
                continue

            stmt_node = stmt_nodes[0]

            # These three dedupe on their own key, before the raw-statement
            # dedup below.
            if language == "pascal":
                imports.extend(_pascal_imports(stmt_node, module_nodes[0], src, seen_pascal_units))
                continue
            if language == "elixir":
                imports.extend(
                    _elixir_imports(stmt_node, module_nodes[0], src, seen_elixir_modules)
                )
                continue
            if language == "fsharp":
                imports.extend(_fsharp_imports(stmt_node, module_nodes[0], src, seen_raws))
                continue

            raw = _node_text(stmt_node, src).strip()
            if raw in seen_raws:
                continue
            seen_raws.add(raw)

            module_text = _node_text(module_nodes[0], src).strip().strip("\"'` ")
            if not module_text:
                continue

            imports.extend(
                _statement_imports(stmt_node, module_nodes[0], module_text, raw, language, src)
            )

        if language == "python":
            imports = expand_bare_relative_imports(imports)

        return imports

    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

    def _extract_calls(
        self,
        matches: list[dict],
        config: LanguageConfig,
        file_info: FileInfo,
        src: str,
        symbols: list[Symbol],
    ) -> list[CallSite]:
        """Extract function/method call sites from the AST."""
        from .language_data import get_builtin_calls

        _call_builtins = get_builtin_calls(file_info.language)

        symbol_ranges = sorted(
            [(s.start_line, s.end_line, s.id) for s in symbols],
            key=lambda t: (t[0], -t[1]),
        )

        calls: list[CallSite] = []

        for capture_dict in matches:
            site_nodes = capture_dict.get("call.site", [])
            target_nodes = capture_dict.get("call.target", [])
            arg_nodes = capture_dict.get("call.arguments", [])
            receiver_nodes = capture_dict.get("call.receiver", [])
            receiver_call_nodes = capture_dict.get("call.receiver_call", [])
            scope_nodes = capture_dict.get("call.scope", [])

            if not site_nodes or not target_nodes:
                continue

            site_node = site_nodes[0]
            target_node = target_nodes[0]
            target_name = config.call_target_name_fn(
                _node_text(target_node, src).strip(), target_node.type
            )
            if not target_name:
                continue

            # Objective-C: a message send binds one `method:` child per
            # keyword, so `[view setTitle:t forState:s]` matches the one
            # query pattern twice. Join the whole selector on the first match
            # so it can meet the symbol side, and drop the rest.
            if file_info.language == "objectivec":
                if site_node.type == "message_expression":
                    joined = _objc_message_selector(site_node, target_nodes[0], src)
                    if joined is None:
                        continue
                    target_name = joined
                # A block held in a parameter or a local is invoked with C call
                # syntax, so `completionBlock(hit)` is indistinguishable from a
                # call to a C function by name alone. Left in, the resolver
                # binds it to whatever same-named @property the repo holds.
                elif not receiver_nodes and _objc_call_is_block_variable(
                    site_node, target_name, src
                ):
                    continue

            if target_name in _call_builtins:
                continue

            # Elixir: a definition head (`add(a, b)` in `def add(a, b)`) and a
            # module attribute (`@doc "..."`) are both ``call`` nodes, and no
            # query predicate can see the parent that tells them apart. Left
            # in, every function in the repo would call itself.
            if file_info.language == "elixir" and _elixir_call_is_definitional(site_node, src):
                continue

            line = site_node.start_point[0] + 1
            receiver_name = _node_text(receiver_nodes[0], src).strip() if receiver_nodes else None
            if receiver_name and file_info.language == "php":
                receiver_name = _normalize_php_receiver(receiver_name)
            # F#: a dotted static path (``Path.Combine(a, b)``) collapses into
            # one identifier node in this grammar -- there is no dot node to
            # capture a receiver from -- so the split happens on the text.
            # After the builtin check above, so a name is filtered as written.
            if file_info.language == "fsharp" and receiver_name is None and "." in target_name:
                receiver_name, _, target_name = target_name.rpartition(".")
                receiver_name = receiver_name.strip()
                target_name = target_name.strip()
                if not target_name:
                    continue
            receiver_call = (
                _call_receiver_from_node(receiver_call_nodes[0], src)
                if receiver_call_nodes
                else None
            )
            scope_name = _node_text(scope_nodes[0], src).strip() if scope_nodes else None

            arg_count: int | None = None
            if arg_nodes:
                arg_node = arg_nodes[0]
                arg_count = _count_arguments(arg_node)

            supplied_props: frozenset[str] | None = None
            if site_node.type in ("jsx_self_closing_element", "jsx_opening_element"):
                props_set: set[str] = set()
                has_spread = False
                for child in site_node.children:
                    if child.type == "jsx_attribute":
                        for sub in child.children:
                            if sub.type in ("property_identifier", "identifier"):
                                props_set.add(_node_text(sub, src))
                                break
                    elif child.type == "jsx_expression":
                        for sub in child.children:
                            if sub.type == "spread_element":
                                has_spread = True
                                break
                if not has_spread:
                    supplied_props = frozenset(props_set)

            caller_id = _find_enclosing_symbol(line, symbol_ranges)

            calls.append(
                CallSite(
                    target_name=target_name,
                    receiver_name=receiver_name,
                    caller_symbol_id=caller_id,
                    line=line,
                    argument_count=arg_count,
                    receiver_call=receiver_call,
                    scope_name=scope_name,
                    edge_type=(
                        "references"
                        if site_node.type in config.reference_call_node_types
                        else "calls"
                    ),
                    supplied_props=supplied_props,
                )
            )

        deduplicated: dict[tuple[int, str, str | None], CallSite] = {}
        for call in calls:
            key = (call.line, call.target_name, call.receiver_name)
            existing = deduplicated.get(key)
            # Two of the three scoped-call patterns can match the same two-part call
            # (one keeps the qualifier, one does not) and both dedup to this key, so
            # the richer record has to win whichever order they arrive in. The
            # three-part pattern (ns::util::fn()) never collides here, it's the
            # only pattern that can match a nested qualified_identifier, so it
            # always lands as a fresh key.
            if (
                existing is None
                or (existing.receiver_call is None and call.receiver_call is not None)
                or (existing.scope_name is None and call.scope_name is not None)
            ):
                deduplicated[key] = call
        return list(deduplicated.values())

    def _extract_references(
        self,
        matches: list[dict],
        file_info: FileInfo,
        src: str,
        symbols: list[Symbol],
    ) -> list[CallSite]:
        """Extract sites that name a function without calling it.

        Six shapes carry a function by name and never call it where a parser
        can see: a dispatch-table entry, a callback field, an argument to a
        registration macro, a func value in argument position, a struct-field
        initialiser, and a ``::`` callable reference. Each leaves the named
        function with no inbound edge, which read as a ``safe_to_delete``
        unused export and took out whole handler and interop layers (#1602).

        ``@reference.receiver`` is optional; capturing it is what lets
        ``_add_reference_edges`` restrict a bare name to free functions and
        allow a qualified name to reach a method.

        Self-gating on the reference captures, so a language whose query
        defines none produces nothing and pays two dict lookups. Two guards
        keep the broad syntactic positions from claiming ordinary code:

        * A macro argument requires a SCREAMING_CASE callee and must be that
          macro's only argument. Uppercase alone is not enough: assertion
          macros are spelled the same way and take values, so ``EXPECT_EQ(
          capacity, 100)`` bound a local to whatever free function shared its
          name. Registering something registers one thing, which separates
          ``BENCHMARK(BM_Foo)`` from ``TEST(SuiteName, TestName)``.
        * A table entry must sit outside any function body. A dispatch table is
          a file-, namespace- or class-scope aggregate; the same braces inside
          a function are a constructor member-init or a local aggregate, where
          ``{data, size}`` names parameters. Measured on fmt, admitting those
          turned ``data``, ``size``, ``begin``, ``end``, ``capacity`` and
          ``buffer`` into edges, every one of them wrong.

        Whether the name denotes a function at all is settled later, at
        resolution, where the symbol kind is known.
        """
        from .language_data import get_builtin_calls

        builtins = get_builtin_calls(file_info.language)

        symbol_ranges = sorted(
            [(s.start_line, s.end_line, s.id) for s in symbols],
            key=lambda t: (t[0], -t[1]),
        )
        callable_ids = {s.id for s in symbols if s.kind in ("function", "method")}

        references: list[CallSite] = []
        seen: set[tuple[int, str, str | None]] = set()

        for capture_dict in matches:
            plain_nodes = capture_dict.get("reference.name", [])
            table_nodes = capture_dict.get("reference.table", [])
            if not plain_nodes and not table_nodes:
                continue

            receiver_nodes = capture_dict.get("reference.receiver", [])
            receiver = (
                _node_text(receiver_nodes[0], src).strip() if receiver_nodes else None
            ) or None

            via_nodes = capture_dict.get("reference.via", [])
            if via_nodes:
                via = _node_text(via_nodes[0], src).strip()
                if not via or not via.isupper():
                    continue
                arg_list = plain_nodes[0].parent if plain_nodes else None
                if arg_list is None or len(arg_list.named_children) != 1:
                    continue

            candidates = [(node, False) for node in plain_nodes]
            candidates += [(node, True) for node in table_nodes]
            for name_node, is_table in candidates:
                name = _node_text(name_node, src).strip()
                if not name or name in builtins:
                    continue
                line = name_node.start_point[0] + 1
                enclosing = _find_enclosing_symbol(line, symbol_ranges)
                if is_table and enclosing in callable_ids:
                    continue
                if (line, name, receiver) in seen:
                    continue
                seen.add((line, name, receiver))
                references.append(
                    CallSite(
                        target_name=name,
                        receiver_name=receiver,
                        caller_symbol_id=enclosing,
                        line=line,
                        argument_count=None,
                    )
                )

        return references

    # ------------------------------------------------------------------
    # Export derivation
    # ------------------------------------------------------------------

    def _derive_exports(
        self,
        symbols: list[Symbol],
        config: LanguageConfig,
    ) -> list[str]:
        """Derive the list of exported names from parsed symbols.

        Note on TS/JS: export visibility is resolved upstream during symbol
        extraction by ``refine_ts_visibility()``, which demotes non-exported
        declarations to private. This expression relies on that classification to
        filter top-level public symbols accurately for TS/JS.
        """
        return [s.name for s in symbols if s.visibility == "public" and s.parent_name is None]

    # ------------------------------------------------------------------
    # Type reference extraction (non-import positions)
    # ------------------------------------------------------------------

    def _extract_type_refs(
        self,
        matches: list[dict],
        src: str,
        lang: str = "",
    ) -> list[TypeReference]:
        """Collect ``@param.type`` captures into TypeReference records.

        C# emits these from constructor / method / delegate / primary-ctor
        parameter types; Go emits them from parameter, struct-field, return,
        and composite-literal type positions (see ``go.scm``). The graph
        builder resolves each reference to a defining file via the
        language-specific resolver index and emits a file-level edge.

        The head-identifier extractor is language-specific (Go unwraps
        ``*T`` / ``[]T`` / ``map[K]V`` / ``pkg.T``); see
        ``TYPE_HEAD_EXTRACTORS``. Capture origin is inferred from the
        enclosing node: ``constructor_declaration`` → ``ctor_param``,
        ``method_declaration`` → ``method_param`` (C#);
        ``field_declaration`` → ``field_type``, ``composite_literal`` →
        ``composite_literal`` (Go).
        """
        head_of = TYPE_HEAD_EXTRACTORS.get(lang, _head_type_identifier)

        refs: list[TypeReference] = []
        seen: set[tuple[str, int]] = set()

        for capture_dict in matches:
            type_nodes = capture_dict.get("param.type", [])
            if not type_nodes:
                continue
            for type_node in type_nodes:
                head = head_of(type_node, src)
                if not head:
                    continue
                # ``struct Wrapper<Item> { value: Item }`` binds Item as a type
                # parameter, and the capture cannot tell that from a reference
                # to a real ``struct Item``. The head extractor drops a
                # single-letter ``T`` but not a named one, so the shadow has to
                # be read off the enclosing item's ``type_parameters``.
                if lang == "rust" and _rust_shadowed_by_type_param(type_node, head, src):
                    continue
                line = type_node.start_point[0] + 1
                key = (head, line)
                if key in seen:
                    continue
                seen.add(key)
                origin = _classify_param_origin(type_node)
                refs.append(TypeReference(type_name=head, line=line, origin=origin))

        return refs


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

_DEFAULT_PARSER: ASTParser | None = None


def parse_file(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Module-level convenience: parse a file using the default ASTParser."""
    global _DEFAULT_PARSER
    if _DEFAULT_PARSER is None:
        _DEFAULT_PARSER = ASTParser()
    return _DEFAULT_PARSER.parse_file(file_info, source)
