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

from .extractors import (
    build_signature,
    extract_go_receiver_type,
    extract_heritage,
    extract_import_bindings,
    extract_module_docstring,
    extract_symbol_docstring,
    node_text,
    refine_go_type_kind,
    refine_kotlin_class_kind,
)
from .extractors.bindings.python import expand_bare_relative_imports
from .extractors.bindings.ts_js import (
    declarator_binds_callable,
    declarator_value_is_module_ref,
)
from .extractors.synthetic_symbols import extract_synthetic_symbols
from .extractors.visibility import (
    refine_cpp_visibility,
    refine_csharp_visibility,
    refine_ts_visibility,
    ts_deferred_export_names,
    ts_export_aliases,
)
from .language_configs import LANGUAGE_CONFIGS, LanguageConfig
from .languages.registry import REGISTRY as _LANG_REGISTRY
from .models import (
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
    _dedupe_pascal_interface_symbols,
    _find_enclosing_symbol,
    _has_callable_ancestor,
    _head_type_identifier,
    _is_async_node,
    _qualified_cpp_parent,
    _qualified_pascal_parent,
    _run_query,
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


# C/C++ node types that spell a type. Each one covers both the definition and
# the forward declaration of that type; only the ``body`` field tells them
# apart. See ``_is_bodiless_cpp_type``.
# ``union_specifier`` is absent because neither grammar's query captures one
# as a symbol today. If a union capture is ever added, add it here too, or a
# bodiless ``union U;`` goes back to reading as a definition.
_CPP_TYPE_SPECIFIER_NODES = frozenset(
    {"class_specifier", "struct_specifier", "enum_specifier"}
)


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
    grammar = grammar_tag or lang
    language = _get_language(grammar)
    if language is None:
        return None

    # The spec names the query file, so a language can reuse another's
    # queries wholesale (svelte -> typescript.scm). Every other spec declares
    # ``<tag>.scm``, which is what the default preserves.
    spec = _LANG_REGISTRY.get(lang)
    scm_name = (spec.scm_file if spec and spec.scm_file else None) or f"{lang}.scm"
    scm_path = QUERIES_DIR / scm_name
    if not scm_path.exists():
        log.debug("No .scm query file found", language=lang, path=str(scm_path))
        return None

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

        return Query(language, scm_text)
    except Exception as exc:
        log.warning("Failed to compile query", language=lang, error=str(exc))
        return None


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
            lang_obj = Language(loader_fn())
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


def _get_language(tag: str) -> Language | None:
    global _LANGUAGE_REGISTRY
    if not _LANGUAGE_REGISTRY:
        _LANGUAGE_REGISTRY = _build_language_registry()
    return _LANGUAGE_REGISTRY.get(tag)


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
        # .tsx files need the JSX-aware grammar; tree-sitter-typescript's
        # default `language_typescript` errors out on every `<Component />`
        # and the resulting ERROR-node recovery hoists nested helpers
        # (handlers defined inside component bodies) to the top level.
        grammar_tag = "tsx" if lang == "typescript" and file_info.path.endswith(".tsx") else lang
        language = _get_language(grammar_tag)

        if config is None or language is None:
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
        exports = self._derive_exports(symbols, config, src)
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
        symbols: list[Symbol] = []
        seen: set[tuple[int, str]] = set()  # (start_line, name) — dedup decorated dupes
        # Parallel to ``symbols`` (same indices) -- only populated/consumed
        # for Pascal, to dedupe interface-declaration vs. implementation
        # method pairs after the loop. See _dedupe_pascal_interface_symbols.
        node_types: list[str] = []

        # Deferred-export names (``export { x }`` / ``export default x``),
        # computed once per file for the TS/JS visibility refinement.
        ts_deferred_exports: frozenset[str] | None = None
        if file_info.language in _TS_JS_LANGUAGES:
            ts_deferred_exports = ts_deferred_export_names(src)

        for capture_dict in matches:
            def_nodes = capture_dict.get("symbol.def", [])
            name_nodes = capture_dict.get("symbol.name", [])
            params_nodes = capture_dict.get("symbol.params", [])
            modifier_nodes = capture_dict.get("symbol.modifiers", [])
            receiver_nodes = capture_dict.get("symbol.receiver", [])

            if not def_nodes or not name_nodes:
                continue

            def_node = def_nodes[0]
            name = _node_text(name_nodes[0], src)
            if not name:
                continue

            start_line = def_node.start_point[0] + 1
            dedup_key = (start_line, name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Kind from node type
            node_type = def_node.type
            kind = config.symbol_node_types.get(node_type)
            if kind is None:
                continue

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
                def_node, config.symbol_node_types
            ):
                continue

            # Refine "struct" kind for Go type_spec (check if struct or interface body)
            if kind == "struct" and config.parent_extraction == "receiver":
                kind = refine_go_type_kind(def_node, src)

            # Refine "class" kind for Kotlin (interface / enum class share class_declaration)
            if (
                kind == "class"
                and file_info.language == "kotlin"
                and def_node.type == "class_declaration"
            ):
                kind = refine_kotlin_class_kind(def_node)

            # Dart: a function is a ``function_signature`` whose BODY is a
            # sibling ``function_body`` node (members wrap the signature in
            # ``method_signature``). Two consequences the generic path can't
            # see: local functions nested inside another function's body have
            # no callable *ancestor* (the enclosing signature is a sibling),
            # so filter them here; and the symbol's line range must extend to
            # the trailing body sibling or call-site attribution stops at the
            # signature line.
            end_line = def_node.end_point[0] + 1
            if file_info.language == "dart" and node_type in (
                "function_signature",
                "getter_signature",
                "setter_signature",
            ):
                ancestor = def_node.parent
                is_local = False
                while ancestor is not None:
                    if ancestor.type in ("function_body", "function_expression"):
                        is_local = True
                        break
                    ancestor = ancestor.parent
                if is_local:
                    continue
                anchor = def_node
                if def_node.parent is not None and def_node.parent.type == "method_signature":
                    anchor = def_node.parent
                body_sibling = anchor.next_named_sibling
                if body_sibling is not None and body_sibling.type == "function_body":
                    end_line = body_sibling.end_point[0] + 1

            # Refine module-level assignments: SCREAMING_CASE names are
            # constants by convention; the rest are module variables
            # (singletons like ``app = FastAPI()``, registries, caches).
            # ``str.isupper()`` requires at least one cased char, so names
            # with no letters (``_``, ``__all__``) fall to "variable" rather
            # than being mislabelled constants by ``name == name.upper()``.
            if node_type in _MODULE_ANCHORED_NODE_TYPES:
                # TS/JS: the symbol query admits call_expression values so
                # forwardRef / memo / onCall / styled() bindings exist at all,
                # which also lets `const svc = require('./svc')` through. Those
                # bind a module and are already imports — drop them here rather
                # than in the query, which cannot see past the await / paren /
                # non-null / member-pick shells.
                if file_info.language in _TS_JS_LANGUAGES and declarator_value_is_module_ref(
                    def_node, src
                ):
                    continue
                # A declarator whose value is structurally callable is not
                # data, whatever its name looks like: `const C =
                # forwardRef(fn)` and `const f = function(){}` are a component
                # and a function. Naming decides only for the rest, which is
                # what it was ever able to answer.
                callable_kind = (
                    declarator_binds_callable(def_node, src)
                    if file_info.language in _TS_JS_LANGUAGES
                    else None
                )
                kind = callable_kind or ("constant" if name.isupper() else "variable")

            # Params signature text
            params_text = _node_text(params_nodes[0], src) if params_nodes else ""

            # Visibility
            modifier_texts = [_node_text(m, src) for m in modifier_nodes]

            # Rust: outer attributes (#[...]) are preceding siblings of the item
            rust_attrs: list[str] = []
            if file_info.language == "rust" and def_node.parent is not None:
                siblings = def_node.parent.children
                for j, sib in enumerate(siblings):
                    if sib.id == def_node.id:
                        k = j - 1
                        while k >= 0 and siblings[k].type == "attribute_item":
                            attr_text = _node_text(siblings[k], src).strip()
                            # Strip #[ and ] to get the inner attribute text
                            if attr_text.startswith("#[") and attr_text.endswith("]"):
                                rust_attrs.append(attr_text[2:-1])
                            k -= 1
                        break

            # C#: [Obsolete] / [System.Obsolete] are ``attribute_list`` nodes.
            # In tree-sitter-c-sharp the attribute_list is child[0] of the
            # declaration node itself (method_declaration, class_declaration, etc.),
            # NOT a preceding sibling in the class body. Iterate def_node.children
            # and collect attribute_list nodes until the first non-attribute child.
            # Strip the outer [ ] so the inner content matches the same
            # _DEPRECATED_DECORATOR_BASES the analyzer uses for every other lang.
            csharp_attrs: list[str] = []
            if file_info.language == "csharp":
                for child in def_node.children:
                    if child.type != "attribute_list":
                        break
                    attr_text = _node_text(child, src).strip()
                    # "[Obsolete]" → "Obsolete"
                    if attr_text.startswith("[") and attr_text.endswith("]"):
                        csharp_attrs.append(attr_text[1:-1])

            # C/C++: [[deprecated]] / [[deprecated("reason")]] are
            # ``attribute_declaration`` nodes. In tree-sitter-cpp the
            # attribute_declaration is child[0] of function_definition itself
            # (NOT a preceding sibling at translation_unit level). Iterate
            # def_node.children and collect attribute_declaration nodes until
            # the first non-attribute child.
            # Strip the outer [[ ]] so the inner content lands in the same
            # checker as the Rust and C# forms.
            cpp_attrs: list[str] = []
            if file_info.language in ("cpp", "c"):
                for child in def_node.children:
                    if child.type != "attribute_declaration":
                        break
                    attr_text = _node_text(child, src).strip()
                    # "[[deprecated]]" → "deprecated"
                    if attr_text.startswith("[[") and attr_text.endswith("]]"):
                        cpp_attrs.append(attr_text[2:-2])

            visibility = config.visibility_fn(name, modifier_texts)
            is_exported_symbol = False
            # C/C++ visibility is dictated by AST context (access
            # specifiers / storage class / export attributes), not by
            # modifier text. Refine after the generic fn ran.
            if file_info.language in ("cpp", "c"):
                visibility, is_exported_symbol = refine_cpp_visibility(def_node, visibility, src)
            # C#: an unmodified declaration's default depends on what encloses
            # it, which the modifier-text fn cannot see.
            elif file_info.language == "csharp":
                visibility = refine_csharp_visibility(def_node, visibility)
            # TS/JS: a top-level declaration is only public when exported —
            # inline, via ``export { x }`` lists, or ``export default x``.
            elif file_info.language in _TS_JS_LANGUAGES:
                visibility = refine_ts_visibility(def_node, visibility, name, ts_deferred_exports)

            # Parent class detection
            parent_name = self._find_parent(def_node, config, receiver_nodes, src)

            # Dart mixin_declaration exposes no ``name`` field, so
            # ``_find_parent``'s field lookup misses mixin members.
            if parent_name is None and file_info.language == "dart":
                ancestor = def_node.parent
                while ancestor is not None:
                    if ancestor.type == "mixin_declaration":
                        ident = next((c for c in ancestor.children if c.type == "identifier"), None)
                        if ident is not None:
                            parent_name = _node_text(ident, src)
                        break
                    if ancestor.type in config.parent_class_types:
                        break
                    ancestor = ancestor.parent

            # C/C++ qualified definitions: ``void Foo::method() { … }``
            # carries the class as the scope of a ``qualified_identifier``
            # parent of the name node. Without this resolution, every
            # ``Class::method`` lands as a free function and bloats the
            # unused_export pass with thousands of method symbols.
            if parent_name is None and file_info.language in ("cpp", "c") and name_nodes:
                parent_name = _qualified_cpp_parent(name_nodes[0], src)

            # Pascal out-of-line implementation: ``function TFoo.Bar(...);``
            # -- the ``defProc`` node lives in the unit's implementation
            # section, outside the class's ``declType`` body declared in the
            # interface section, so nesting-based ``_find_parent`` above
            # can't see it. The qualifying class lives beside the captured
            # name in the ``genericDot`` header instead.
            if parent_name is None and file_info.language == "pascal" and name_nodes:
                parent_name = _qualified_pascal_parent(name_nodes[0], src)

            # Upgrade function → method when a parent class is detected
            if parent_name and kind == "function":
                kind = "method"

            # Build signature
            signature = build_signature(node_type, name, params_text, def_node, src)

            # Docstring
            docstring = extract_symbol_docstring(def_node, src, file_info.language)

            # Async detection
            is_async = _is_async_node(def_node, src)

            sym_id = (
                f"{file_info.path}::{parent_name}::{name}"
                if parent_name
                else f"{file_info.path}::{name}"
            )
            qualified = _build_qualified_name(file_info.path, parent_name, name)

            symbols.append(
                Symbol(
                    id=sym_id,
                    name=name,
                    qualified_name=qualified,
                    kind=kind,  # type: ignore[arg-type]
                    signature=signature,
                    start_line=start_line,
                    end_line=end_line,
                    docstring=docstring,
                    decorators=(
                        [m for m in modifier_texts if m.startswith("@")]
                        + rust_attrs
                        + csharp_attrs
                        + cpp_attrs
                    ),
                    visibility=visibility,  # type: ignore[arg-type]
                    is_async=is_async,
                    language=file_info.language,
                    parent_name=parent_name,
                    is_exported_symbol=is_exported_symbol,
                    is_declaration=(
                        node_type in config.declaration_node_types
                        or _is_bodiless_cpp_type(file_info.language, node_type, def_node)
                    ),
                )
            )
            node_types.append(node_type)

        if file_info.language == "pascal":
            symbols = _dedupe_pascal_interface_symbols(symbols, node_types)

        return symbols

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
        imports: list[Import] = []
        seen_raws: set[str] = set()
        seen_pascal_units: set[str] = set()

        for capture_dict in matches:
            stmt_nodes = capture_dict.get("import.statement", [])
            module_nodes = capture_dict.get("import.module", [])

            if not stmt_nodes or not module_nodes:
                continue

            stmt_node = stmt_nodes[0]

            # Pascal: `uses UnitA, UnitB, Ns.UnitC;` -- pascal.scm's
            # unquantified pattern (see that file's comment on why) fires
            # once PER moduleName, so a 3-unit clause arrives as 3 separate
            # matches sharing one @import.statement span, each carrying a
            # single-element ``module_nodes``. Handled before the
            # ``seen_raws`` dedup below: that guard exists to skip a
            # statement re-matched by an *overlapping* pattern (the normal
            # case elsewhere), but here every match legitimately carries a
            # different unit despite the identical raw statement text --
            # dedup-by-raw would keep only the first and silently drop the
            # rest, which is exactly the bug this branch fixes.
            #
            # Deduped separately by unit name (case-insensitive -- Pascal
            # identifiers are): a unit named in both the ``interface`` and
            # ``implementation`` ``uses`` clauses of the same file is a
            # single logical dependency and must not become two Import
            # entries for it.
            if file_info.language == "pascal":
                raw = _node_text(stmt_node, src).strip()
                unit_name = _node_text(module_nodes[0], src).strip()
                if unit_name and unit_name.lower() not in seen_pascal_units:
                    seen_pascal_units.add(unit_name.lower())
                    imports.append(
                        Import(
                            raw_statement=raw,
                            module_path=unit_name,
                            imported_names=[],
                            is_relative=False,
                            resolved_file=None,
                            bindings=[],
                            is_reexport=False,
                        )
                    )
                continue

            raw = _node_text(stmt_node, src).strip()
            if raw in seen_raws:
                continue
            seen_raws.add(raw)

            module_text = _node_text(module_nodes[0], src).strip().strip("\"'` ")
            if not module_text:
                continue

            # Scala: the query's ``(identifier)`` capture is only the FIRST
            # path segment (``import com.foo.Bar`` arrived as ``com``), and
            # one declaration can hold several clauses, brace selectors,
            # renames, and wildcards. Reconstruct full dotted paths and emit
            # one Import per selected name.
            if file_info.language == "scala" and stmt_node.type == "import_declaration":
                from .extractors.bindings.scala import expand_scala_import_clauses
                from .models import NamedBinding

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
                continue

            # Dart: URIs are relative unless schemed (``package:``/``dart:``),
            # ``export`` directives are barrel re-exports, and the legacy
            # dotted ``part of library.name;`` form resolves through the
            # library-name index (the ``library:`` prefix is the resolver's
            # contract, shared with the lightweight regex tier).
            if file_info.language == "dart":
                module_path = module_text
                if module_nodes[0].type == "dotted_identifier_list":
                    module_path = f"library:{module_text}"
                imported_names, bindings = extract_import_bindings(
                    stmt_node, src, file_info.language
                )
                imports.append(
                    Import(
                        raw_statement=raw,
                        module_path=module_path,
                        imported_names=imported_names,
                        is_relative=not module_path.startswith(("package:", "dart:", "library:")),
                        resolved_file=None,
                        bindings=bindings,
                        is_reexport=stmt_node.type == "library_export",
                    )
                )
                continue

            # Dynamic ESM import: ``import('./mod')``. The query captures the
            # call_expression, which would otherwise fall into the CommonJS
            # branch below and be dropped on the floor — a dynamic import
            # holds no ``require()`` for ``collect_cjs_requires`` to find.
            # The construct binds a module namespace at runtime, so record a
            # wildcard rather than a static name.  Downstream unused-export
            # analysis treats ``*`` as namespace consumption and therefore
            # keeps the target's exports live without a broad analyzer
            # exemption.
            if (
                file_info.language in _TS_JS_LANGUAGES
                and stmt_node.type == "call_expression"
                and (_fn := stmt_node.child_by_field_name("function")) is not None
                and _fn.type == "import"
            ):
                imports.append(
                    Import(
                        raw_statement=raw,
                        module_path=module_text,
                        imported_names=["*"],
                        is_relative=module_text.startswith("."),
                        resolved_file=None,
                        bindings=[],
                        is_reexport=False,
                    )
                )
                continue

            # CommonJS assignment / Object.assign shapes: the query captures
            # the outer statement once; walk it for every require() it
            # contains (a hub like Object.assign(module.exports,
            # require('./a'), require('./b')) is several imports) and mark
            # module.exports/exports shapes as re-exports so barrel logic
            # treats CJS hubs like ESM barrels.
            if file_info.language in ("javascript", "typescript") and stmt_node.type in (
                "assignment_expression",
                "call_expression",
            ):
                from .extractors.bindings.ts_js import (
                    cjs_statement_is_reexport,
                    collect_cjs_requires,
                )

                cjs_reexport = cjs_statement_is_reexport(stmt_node, src)
                for cjs_module in collect_cjs_requires(stmt_node, src):
                    imports.append(
                        Import(
                            raw_statement=raw,
                            module_path=cjs_module,
                            imported_names=["*"] if cjs_reexport else [],
                            is_relative=cjs_module.startswith("."),
                            resolved_file=None,
                            bindings=[],
                            is_reexport=cjs_reexport,
                        )
                    )
                continue

            # Rust #[path = "..."] attribute overrides module file location.
            # In tree-sitter-rust, outer attributes are preceding siblings of
            # the item, not children.
            if file_info.language == "rust" and stmt_node.type == "mod_item":
                parent = stmt_node.parent
                if parent is not None:
                    siblings = parent.children
                    for j, sib in enumerate(siblings):
                        if sib.id == stmt_node.id:
                            # Walk backward through preceding attribute_item siblings
                            k = j - 1
                            while k >= 0 and siblings[k].type == "attribute_item":
                                attr_text = _node_text(siblings[k], src)
                                path_match = re.search(r'path\s*=\s*"([^"]+)"', attr_text)
                                if path_match:
                                    module_text = path_match.group(1)
                                    break
                                k -= 1
                            break

            # JVM wildcard imports: the grammar query captures the scoped
            # identifier only — the trailing ``*`` is a sibling node, so
            # ``import com.foo.*`` arrives as ``com.foo`` and the resolvers'
            # package fan-out branch can never fire. Restore it from the
            # raw statement text.
            if file_info.language in ("java", "kotlin") and not module_text.endswith("*"):
                stmt_text = raw.rstrip().rstrip(";").rstrip()
                if stmt_text.endswith(".*"):
                    module_text += ".*"

            # Language-specific import name + binding extraction
            imported_names, bindings = extract_import_bindings(stmt_node, src, file_info.language)
            is_relative = (
                module_text.startswith(".")
                or module_text.startswith("./")
                or module_text.startswith(("self::", "super::", "crate::"))
            )

            is_reexport = False
            if file_info.language == "rust" and stmt_node.type == "use_declaration":
                for child in stmt_node.children:
                    if child.type == "visibility_modifier":
                        is_reexport = True
                        break
            # Swift: ``@_exported import FooKit`` re-exports the module —
            # importers of THIS module see FooKit's symbols too.
            elif file_info.language == "swift" and raw.startswith("@_exported"):
                is_reexport = True

            imports.append(
                Import(
                    raw_statement=raw,
                    module_path=module_text,
                    imported_names=imported_names,
                    is_relative=is_relative,
                    resolved_file=None,
                    bindings=bindings,
                    is_reexport=is_reexport,
                )
            )

        if file_info.language == "python":
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
        seen: set[tuple[int, str, str | None]] = set()

        for capture_dict in matches:
            site_nodes = capture_dict.get("call.site", [])
            target_nodes = capture_dict.get("call.target", [])
            arg_nodes = capture_dict.get("call.arguments", [])
            receiver_nodes = capture_dict.get("call.receiver", [])

            if not site_nodes or not target_nodes:
                continue

            site_node = site_nodes[0]
            target_name = _node_text(target_nodes[0], src).strip()
            if not target_name:
                continue

            if target_name in _call_builtins:
                continue

            line = site_node.start_point[0] + 1
            receiver_name = _node_text(receiver_nodes[0], src).strip() if receiver_nodes else None
            if receiver_name and file_info.language == "php":
                receiver_name = _normalize_php_receiver(receiver_name)

            dedup_key = (line, target_name, receiver_name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            arg_count: int | None = None
            if arg_nodes:
                arg_node = arg_nodes[0]
                arg_count = _count_arguments(arg_node)

            caller_id = _find_enclosing_symbol(line, symbol_ranges)

            calls.append(
                CallSite(
                    target_name=target_name,
                    receiver_name=receiver_name,
                    caller_symbol_id=caller_id,
                    line=line,
                    argument_count=arg_count,
                )
            )

        return calls

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
        src: str,
    ) -> list[str]:
        """Derive the list of exported names from parsed symbols."""
        if config.export_node_types:
            return [s.name for s in symbols if s.visibility == "public" and s.parent_name is None]
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
