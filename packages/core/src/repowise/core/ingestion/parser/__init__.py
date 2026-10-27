"""Unified AST parser — one class for all languages.

Architecture
============
Per-language differences live in two places:
  1. ``packages/core/queries/<lang>.scm``  — tree-sitter S-expression queries
     that capture symbols and imports using consistent capture-name conventions.
  2. ``LANGUAGE_CONFIGS`` (``language_configs.py``) — a ``LanguageConfig`` per
     language that maps node types to symbol kinds, defines visibility rules,
     and names its import extractor (``extractors/imports/<lang>.py``).

The package splits one parse into its passes: ``grammars`` (grammar and query
loading, the tree-sitter parse), ``symbols`` and ``symbol_rules``, ``imports``,
``calls`` and ``references``. Every name the old single module defined is
re-exported here.

Capture-name conventions (shared across ALL .scm files):
  @symbol.def       — the full definition node (line numbers, kind lookup)
  @symbol.name      — name identifier
  @symbol.params    — parameter list (optional)
  @symbol.modifiers — decorators / visibility modifiers (optional)
  @symbol.receiver  — Go method receiver (optional, used for parent detection)
  @import.statement — full import node
  @import.module    — module path being imported
"""

# ruff: noqa: F401

from __future__ import annotations

from tree_sitter import Language

from ..extractors import extract_heritage, extract_module_docstring
from ..extractors.imports.dart import _dart_import
from ..extractors.imports.elixir import _elixir_imports
from ..extractors.imports.fsharp import _fsharp_imports
from ..extractors.imports.generic import _generic_import
from ..extractors.imports.pascal import _pascal_imports
from ..extractors.imports.php import _php_imports
from ..extractors.imports.rust import (
    _rust_inline_mod_depth,
    _rust_mod_path_attribute,
    _rust_use_imports,
    _strip_inline_super,
)
from ..extractors.imports.scala import _scala_imports
from ..extractors.imports.ts_js import _cjs_imports, _is_dynamic_esm_import
from ..extractors.visibility import ts_export_aliases
from ..language_configs import LANGUAGE_CONFIGS, LanguageConfig
from ..models import FileInfo, Import, ParsedFile, Symbol, compute_content_hash
from ..parser_helpers import _collect_error_nodes, _run_query
from ..python_local_refs import local_refs_from_tree, parse_python_module
from ..python_registrars import registering_functions
from ..salesforce_soql import apex_schema_refs
from ..sfc_source import component_call_sites, prepare_source
from ..special_handlers import SPECIAL_HANDLER_LANGUAGES, parse_special
from .calls import (
    _DOTTED_PATH,
    _PATH_RECEIVERS,
    _call_receiver_from_node,
    _dart_argument_count,
    _dart_call_receiver,
    _dedupe_calls,
    _extract_calls,
    _fold_receiverless_twins,
    _is_bare,
    _jsx_supplied_props,
    _normalize_php_receiver,
    _objc_call_target,
    _receiver_text,
)
from .grammars import (
    _LANGUAGE_REGISTRY,
    _MISSING_GRAMMAR_REPORTED,
    _PASSTHROUGH_LANGUAGES,
    QUERIES_DIR,
    _compile_query,
    _get_language,
    _load_compiled_query,
    _parse_tree,
    _recover_kotlin_annotated_declarations,
    failed_query_languages,
    grammar_tag_for,
    log,
    missing_grammar_languages,
    unavailable_runtime_grammars,
)
from .imports import _extract_imports
from .references import (
    _NAMED_BINDING_LANGUAGES,
    _REFERENCE_LANGUAGES,
    _bare_reference_names,
    _extract_references,
    _extract_type_refs,
)
from .symbol_rules import (
    _DART_FUNCTION_NODE_TYPES,
    _FSHARP_BINDING_NODE_TYPES,
    _TS_JS_LANGUAGES,
    _TS_OVERLOAD_SIGNATURES,
    _attribute_decorators,
    _dart_function_end_line,
    _dart_mixin_parent,
    _declared_modifiers,
    _drop_swift_extensions_of_local_types,
    _is_declaration,
    _is_fsharp_binding,
    _is_split_annotation_chain,
    _keep_ts_overloads_beside_a_body,
    _kotlin_detached_annotation,
    _language_symbol_name,
    _leading_child_attributes,
    _module_binding_kind,
    _node_text,
    _refine_symbol_kind,
    _refine_visibility,
    _restore_kotlin_annotations,
    _rust_outer_attributes,
    _symbol_end_line,
)
from .symbols import (
    _MODULE_ANCHORED_NODE_TYPES,
    _extract_symbols,
    _finalize_symbols,
    _java_enum_constant,
    _match_identity,
    _nesting_parent,
    _normalize_newlines,
    _parent_type_name,
)

# A file with more symbols than this is almost certainly generated; warn, never truncate.
_SYMBOL_COUNT_WARN_THRESHOLD = 500


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

    def parse_imports(self, file_info: FileInfo, source: bytes) -> list[Import]:
        """Only *source*'s imports: :meth:`parse_file`'s grammar, query and import pass.

        Symbols, calls and references are not extracted, which is most of a
        parse. A language without a tree-sitter path takes the full parse.
        """
        lang = file_info.language
        config = LANGUAGE_CONFIGS.get(lang)
        grammar_tag = grammar_tag_for(lang, file_info.path)
        language = _get_language(grammar_tag)
        if (
            lang in SPECIAL_HANDLER_LANGUAGES
            or config is None
            or language is None
            or _is_fsharp_signature(file_info)
        ):
            return self.parse_file(file_info, source).imports
        original_source = source
        source = prepare_source(lang, source, path=file_info.path)
        root, _, grammar_tag, language = _parse_tree(lang, language, grammar_tag, source)
        query = _load_compiled_query(lang, grammar_tag)
        matches = _run_query(query, root) if query is not None else []
        src = source.decode("utf-8", errors="replace")
        imports = _extract_imports(matches, config, file_info, src)
        return imports + _masked_imports(lang, original_source)

    def parse_file(self, file_info: FileInfo, source: bytes) -> ParsedFile:
        """Parse *source* bytes and return a fully populated ParsedFile."""
        lang = file_info.language
        content_hash = compute_content_hash(source)

        # Non-tree-sitter formats (OpenAPI, Dockerfile, Makefile, SQL) carry no
        # LanguageConfig, so they are routed before the no-grammar fallback.
        if lang in SPECIAL_HANDLER_LANGUAGES:
            parsed = parse_special(file_info, source, lang)
            parsed.content_hash = content_hash
            return parsed

        config = LANGUAGE_CONFIGS.get(lang)
        grammar_tag = grammar_tag_for(lang, file_info.path)
        language = _get_language(grammar_tag)
        if config is None or language is None or _is_fsharp_signature(file_info):
            return _parse_without_grammar(file_info, source, config, language, content_hash)

        # An SFC (.svelte, .vue) is blanked to its script at byte-identical
        # offsets; ``content_hash`` above keeps hashing the original bytes.
        original_source = source
        source = prepare_source(lang, source, path=file_info.path)

        root, parse_errors, grammar_tag, language = _parse_tree(lang, language, grammar_tag, source)

        recovered_annotations: dict[int, tuple[int, str]] = {}
        if lang == "kotlin":
            root, source, recovered_annotations = _recover_kotlin_annotated_declarations(
                language,
                root,
                source,
            )
            if recovered_annotations:
                parse_errors = _collect_error_nodes(root)

        src = source.decode("utf-8", errors="replace")

        # Execute the compiled query ONCE per file; every pass below reads
        # the same capture dicts.
        query = _load_compiled_query(lang, grammar_tag)
        matches = _run_query(query, root) if query is not None else []

        symbols = _finalize_symbols(
            _extract_symbols(matches, config, file_info, src, original_source), root, src, file_info
        )
        _restore_kotlin_annotations(symbols, recovered_annotations)
        imports = _extract_imports(matches, config, file_info, src)
        imports.extend(_masked_imports(lang, original_source))
        calls = _extract_calls(matches, config, file_info, src, symbols)
        # An SFC's markup tags (``<Foo />``) are component calls the TS buffer lost.
        calls.extend(component_call_sites(lang, original_source, symbols))
        calls.extend(_masked_call_sites(lang, original_source, symbols))
        references = (
            _extract_references(matches, file_info, src, symbols, imports)
            if lang in _REFERENCE_LANGUAGES
            else []
        )
        # Python names used in their own module outside a call or import,
        # stamped so dead-code can rescue them.
        local_refs, registrars = (
            _python_module_facts(src, symbols) if lang == "python" else (frozenset(), frozenset())
        )
        _warn_symbol_bloat(file_info, symbols)

        return ParsedFile(
            file_info=file_info,
            symbols=symbols,
            imports=imports,
            exports=_derive_exports(symbols),
            export_aliases=ts_export_aliases(src) if lang in _TS_JS_LANGUAGES else {},
            calls=calls,
            heritage=extract_heritage(matches, config, file_info, src),
            docstring=_normalize_newlines(extract_module_docstring(root, src, lang)),
            parse_errors=parse_errors,
            content_hash=content_hash,
            type_refs=_extract_type_refs(matches, src, lang),
            local_refs=local_refs,
            registrars=registrars,
            references=references,
            # SOQL/SOSL, DML and trigger targets, bound to SObject metadata later.
            schema_refs=(
                apex_schema_refs(root, symbols, file_info.path) if lang == "apex" else []
            ),
        )


def _is_fsharp_signature(file_info: FileInfo) -> bool:
    """An F# ``.fsi`` signature file needs a grammar the spec does not load.

    Read with the implementation grammar, its ``val`` signatures land in
    ERROR recovery; the regex tier still gives it its ``open`` imports.
    """
    return file_info.language == "fsharp" and file_info.path.endswith(".fsi")


def _parse_without_grammar(
    file_info: FileInfo,
    source: bytes,
    config: LanguageConfig | None,
    language: Language | None,
    content_hash: str,
) -> ParsedFile:
    """Regex-tier imports and no symbols, for a file with no usable grammar."""
    lang = file_info.language
    if config is not None and language is None and lang not in _MISSING_GRAMMAR_REPORTED:
        # Once per language, not once per file: the fact is about the environment.
        _MISSING_GRAMMAR_REPORTED.add(lang)
        log.debug("tree-sitter grammar unavailable", language=lang)
    from ..lightweight_imports import extract_lightweight_imports

    return ParsedFile(
        file_info=file_info,
        symbols=[],
        imports=extract_lightweight_imports(file_info, source),
        exports=[],
        docstring=None,
        parse_errors=[],
        content_hash=content_hash,
        grammar_missing=config is not None and language is None,
    )


def _masked_imports(lang: str, original_source: bytes) -> list[Import]:
    """Imports ``prepare_source`` masked from the grammar, read from the original bytes."""
    if lang == "cobol":
        from ..cobol.facts import cobol_imports

        return cobol_imports(original_source)
    return []


def _masked_call_sites(lang: str, original_source: bytes, symbols: list[Symbol]) -> list:
    """Call sites ``prepare_source`` masked from the grammar (COBOL ``EXEC CICS``)."""
    if lang == "cobol":
        from ..cobol.facts import cobol_call_sites

        return cobol_call_sites(original_source, symbols)
    return []


def _warn_symbol_bloat(file_info: FileInfo, symbols: list[Symbol]) -> None:
    # COBOL's only children are level-88 condition names: values of one data
    # item, not declarations. Methods still count, so a generated class warns.
    counted = sum(1 for s in symbols if not (s.language == "cobol" and s.parent_name))
    if counted > _SYMBOL_COUNT_WARN_THRESHOLD:
        log.warning(
            "parser.symbol_bloat",
            path=file_info.path,
            language=file_info.language,
            symbol_count=counted,
            threshold=_SYMBOL_COUNT_WARN_THRESHOLD,
        )


def _derive_exports(symbols: list[Symbol]) -> list[str]:
    """Top-level public symbol names.

    TS/JS visibility was already refined to "exported" during symbol extraction.
    """
    return [s.name for s in symbols if s.visibility == "public" and s.parent_name is None]


_DEFAULT_PARSER: ASTParser | None = None


def _python_module_facts(src: str, symbols: list[Symbol]) -> tuple[frozenset[str], frozenset[str]]:
    """``(local_refs, registrars)`` for a Python module, from one ``ast`` parse.

    See ``python_local_refs`` and ``python_registrars``.
    """
    top_level_names = {s.name for s in symbols if s.name and not s.parent_name}
    tree = parse_python_module(src) if top_level_names else None
    if tree is None:
        return frozenset(), frozenset()
    nested_classes = {
        (s.parent_name, s.name) for s in symbols if s.kind == "class" and s.parent_name
    }
    return (
        local_refs_from_tree(tree, top_level_names, nested_classes),
        registering_functions(tree),
    )


def parse_file(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Module-level convenience: parse a file using the default ASTParser."""
    global _DEFAULT_PARSER
    if _DEFAULT_PARSER is None:
        _DEFAULT_PARSER = ASTParser()
    return _DEFAULT_PARSER.parse_file(file_info, source)
