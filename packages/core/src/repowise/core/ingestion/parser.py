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

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
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
from .extractors.synthetic_symbols import extract_synthetic_symbols
from .extractors.visibility import (
    csharp_visibility,
    go_visibility,
    java_visibility,
    kotlin_visibility,
    php_visibility,
    public_by_default,
    py_visibility,
    refine_cpp_visibility,
    rust_visibility,
    scala_visibility,
    swift_visibility,
    ts_visibility,
)
from .languages.registry import REGISTRY as _LANG_REGISTRY
from .models import (
    CallSite,
    FileInfo,
    Import,
    ParsedFile,
    Symbol,
    TypeReference,
)

log = structlog.get_logger(__name__)

# Any single file emitting more than this many symbols is almost
# certainly machine-generated (large gRPC service contracts, OpenAPI
# bindings, SQL schema bindings). Warn rather than truncate — operators
# can decide whether to add the file to ``_NEVER_FLAG_PATTERNS`` or to
# exclude it via traversal.
_SYMBOL_COUNT_WARN_THRESHOLD = 500

QUERIES_DIR = Path(__file__).parent / "queries"


@lru_cache(maxsize=None)
def _load_compiled_query(lang: str) -> object | None:
    """Process-wide cache of compiled tree-sitter Query objects by language tag.

    Compiling `.scm` queries is non-trivial; in process-pool parsing each worker
    would otherwise recompile per file. Keyed by lang because `_get_language`
    returns a stable Language singleton per tag within a process.
    """
    language = _get_language(lang)
    if language is None:
        return None

    scm_path = QUERIES_DIR / f"{lang}.scm"
    if not scm_path.exists():
        log.debug("No .scm query file found", language=lang, path=str(scm_path))
        return None

    scm_text = scm_path.read_text(encoding="utf-8")
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
_PASSTHROUGH_LANGUAGES: frozenset[str] = frozenset(
    spec.tag
    for spec in _LANG_REGISTRY.all_specs()
    if spec.is_passthrough
    and (not spec.is_code or spec.is_infra)
    and spec.tag not in ("openapi", "unknown")
)

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


def _get_language(tag: str) -> Language | None:
    global _LANGUAGE_REGISTRY
    if not _LANGUAGE_REGISTRY:
        _LANGUAGE_REGISTRY = _build_language_registry()
    return _LANGUAGE_REGISTRY.get(tag)


# ---------------------------------------------------------------------------
# LanguageConfig
# ---------------------------------------------------------------------------

# Private alias for internal use (kept for compatibility with _find_parent)
_node_text = node_text


@dataclass
class LanguageConfig:
    """Per-language metadata used by ASTParser.

    The ASTParser itself contains no language-specific if/elif logic.
    All branching happens through these configs and the .scm query files.
    """

    # Maps tree-sitter node type → our canonical SymbolKind string
    symbol_node_types: dict[str, str]

    # tree-sitter node types that carry import information (doc purposes)
    import_node_types: list[str]

    # tree-sitter node types that export symbols (doc purposes)
    export_node_types: list[str]

    # (name: str, modifier_texts: list[str]) → "public" | "private" | ...
    visibility_fn: Callable[[str, list[str]], str]

    # How to determine a method's parent class:
    #   "nesting"  — walk up AST; parent class types in parent_class_types
    #   "receiver" — extract from @symbol.receiver capture (Go)
    #   "impl"     — look for impl_item ancestor (Rust)
    #   "none"     — no parent tracking
    parent_extraction: str = "nesting"

    # Node types that indicate a class context (used with "nesting" mode)
    parent_class_types: frozenset[str] = field(default_factory=frozenset)

    # Entry-point filename patterns for this language
    entry_point_patterns: list[str] = field(default_factory=list)


LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {
    "python": LanguageConfig(
        symbol_node_types={
            "function_definition": "function",
            "class_definition": "class",
        },
        import_node_types=["import_statement", "import_from_statement"],
        export_node_types=[],
        visibility_fn=py_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_definition"}),
        entry_point_patterns=["main.py", "app.py", "__main__.py", "manage.py", "wsgi.py"],
    ),
    "typescript": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "generator_function_declaration": "function",
            "arrow_function": "function",
            "class_declaration": "class",
            "abstract_class_declaration": "class",
            "interface_declaration": "interface",
            "type_alias_declaration": "type_alias",
            "enum_declaration": "enum",
            "method_definition": "method",
            "lexical_declaration": "function",  # const foo = () => {}
        },
        import_node_types=["import_statement"],
        export_node_types=["export_statement"],
        visibility_fn=ts_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_declaration", "abstract_class_declaration"}),
        entry_point_patterns=["index.ts", "main.ts", "app.ts", "server.ts"],
    ),
    "javascript": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "generator_function_declaration": "function",
            "arrow_function": "function",
            "class_declaration": "class",
            "method_definition": "method",
            "lexical_declaration": "function",
        },
        import_node_types=["import_statement"],
        export_node_types=["export_statement"],
        visibility_fn=public_by_default,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_declaration"}),
        entry_point_patterns=["index.js", "main.js", "app.js", "server.js"],
    ),
    "go": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "method_declaration": "method",
            "type_spec": "struct",  # refined in post-processing
            "const_spec": "variable",  # const MaxRetries = 3
            "var_spec": "variable",  # var ErrNotFound = errors.New(...)
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=go_visibility,
        parent_extraction="receiver",
        parent_class_types=frozenset(),
        entry_point_patterns=["main.go", "cmd/main.go"],
    ),
    "rust": LanguageConfig(
        symbol_node_types={
            "function_item": "function",
            "struct_item": "struct",
            "enum_item": "enum",
            "trait_item": "trait",
            "impl_item": "impl",
            "const_item": "constant",
            "type_item": "type_alias",
            "mod_item": "module",
            "macro_definition": "function",  # macro_rules! my_macro { ... }
        },
        import_node_types=["use_declaration"],
        export_node_types=[],
        visibility_fn=rust_visibility,
        parent_extraction="impl",
        parent_class_types=frozenset({"impl_item"}),
        entry_point_patterns=["main.rs", "lib.rs"],
    ),
    "java": LanguageConfig(
        symbol_node_types={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "class",  # Java 16+ records
            "method_declaration": "method",
            "constructor_declaration": "function",
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=java_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset(
            {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
        ),
        entry_point_patterns=["Main.java", "Application.java"],
    ),
    "cpp": LanguageConfig(
        symbol_node_types={
            "function_definition": "function",
            "class_specifier": "class",
            "struct_specifier": "struct",
            "enum_specifier": "enum",
            "namespace_definition": "module",
            "template_declaration": "class",  # template<> class/struct/function
            "type_definition": "struct",  # typedef struct { ... } Name;
            "preproc_def": "variable",  # #define MACRO value
            "preproc_function_def": "function",  # #define MACRO(x) ...
            "declaration": "function",  # forward declarations
        },
        import_node_types=["preproc_include"],
        export_node_types=[],
        visibility_fn=public_by_default,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_specifier", "struct_specifier"}),
        entry_point_patterns=["main.cpp", "main.cc"],
    ),
    "c": LanguageConfig(
        symbol_node_types={
            "function_definition": "function",
            "struct_specifier": "struct",
            "enum_specifier": "enum",
            "type_definition": "struct",  # typedef struct { ... } Name;
            "preproc_def": "variable",  # #define MACRO value
            "preproc_function_def": "function",  # #define MACRO(x) ...
            "declaration": "function",  # forward declarations
        },
        import_node_types=["preproc_include"],
        export_node_types=[],
        visibility_fn=public_by_default,
        parent_extraction="none",
        parent_class_types=frozenset(),
        entry_point_patterns=["main.c"],
    ),
    "kotlin": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "class_declaration": "class",
            "object_declaration": "class",
            "type_alias": "type_alias",
            "property_declaration": "variable",
        },
        import_node_types=["import"],
        export_node_types=[],
        visibility_fn=kotlin_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_declaration", "object_declaration"}),
        entry_point_patterns=["Main.kt", "Application.kt"],
    ),
    "ruby": LanguageConfig(
        symbol_node_types={
            "method": "function",
            "singleton_method": "function",
            "class": "class",
            "module": "module",
            "assignment": "constant",
        },
        import_node_types=["call"],
        export_node_types=[],
        visibility_fn=public_by_default,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class", "module"}),
        entry_point_patterns=["main.rb", "app.rb", "config.ru"],
    ),
    "csharp": LanguageConfig(
        symbol_node_types={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "struct_declaration": "struct",
            "enum_declaration": "enum",
            "enum_member_declaration": "variable",
            "method_declaration": "method",
            "constructor_declaration": "function",
            "property_declaration": "variable",
            "field_declaration": "variable",
            "record_declaration": "class",
            "delegate_declaration": "function",
            "event_declaration": "variable",
            "event_field_declaration": "variable",
            "namespace_declaration": "module",
            "file_scoped_namespace_declaration": "module",
        },
        import_node_types=["using_directive", "global_using_directive"],
        export_node_types=[],
        visibility_fn=csharp_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset(
            {
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "enum_declaration",
                "record_declaration",
                "namespace_declaration",
                "file_scoped_namespace_declaration",
            }
        ),
        entry_point_patterns=["Program.cs", "Startup.cs"],
    ),
    "swift": LanguageConfig(
        symbol_node_types={
            "class_declaration": "class",
            "protocol_declaration": "interface",
            "function_declaration": "function",
            "protocol_function_declaration": "function",
            "property_declaration": "variable",
            "subscript_declaration": "method",
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=swift_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_declaration", "protocol_declaration"}),
        entry_point_patterns=["main.swift", "App.swift"],
    ),
    "scala": LanguageConfig(
        symbol_node_types={
            "class_definition": "class",
            "trait_definition": "trait",
            "object_definition": "class",
            "function_definition": "function",
            "function_declaration": "function",
            "val_definition": "variable",
            "var_definition": "variable",
            "enum_definition": "enum",
            "given_definition": "variable",
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=scala_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_definition", "trait_definition", "object_definition"}),
        entry_point_patterns=["Main.scala", "App.scala"],
    ),
    "php": LanguageConfig(
        symbol_node_types={
            "class_declaration": "class",
            "interface_declaration": "interface",
            "trait_declaration": "trait",
            "enum_declaration": "enum",
            "method_declaration": "method",
            "function_definition": "function",
            "const_declaration": "constant",
            "property_declaration": "variable",
        },
        import_node_types=["namespace_use_declaration"],
        export_node_types=[],
        visibility_fn=php_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset(
            {"class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"}
        ),
        entry_point_patterns=["index.php", "public/index.php"],
    ),
    "luau": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "type_definition": "type_alias",
        },
        import_node_types=["function_call"],
        export_node_types=[],
        visibility_fn=public_by_default,
        parent_extraction="none",
        parent_class_types=frozenset(),
        entry_point_patterns=["init.luau", "init.lua"],
    ),
}


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
        config = LANGUAGE_CONFIGS.get(lang)
        language = _get_language(lang)

        if config is None or language is None:
            if config is not None and language is None:
                log.debug(
                    "tree-sitter grammar unavailable",
                    language=lang,
                    path=file_info.path,
                )
            return ParsedFile(
                file_info=file_info,
                symbols=[],
                imports=[],
                exports=[],
                docstring=None,
                parse_errors=[],
            )

        # Delegate to special handlers for non-tree-sitter formats
        if lang in ("openapi", "dockerfile", "makefile"):
            from .special_handlers import parse_special

            return parse_special(file_info, source, lang)

        parser = Parser(language)
        tree = parser.parse(source)
        src = source.decode("utf-8", errors="replace")
        root = tree.root_node

        parse_errors = _collect_error_nodes(root)
        query = self._get_query(lang, language)

        symbols = self._extract_symbols(tree, query, config, file_info, src)
        # Per-language synthetic-symbol pass — recognises source-generator
        # attributes (e.g. CommunityToolkit.Mvvm) and adds the symbols the
        # generator would emit at compile time. No-op for languages
        # without a registered extractor.
        synthetic = extract_synthetic_symbols(root, src, file_info)
        if synthetic:
            existing_ids = {s.id for s in symbols}
            symbols.extend(s for s in synthetic if s.id not in existing_ids)
        imports = self._extract_imports(tree, query, config, file_info, src)
        calls = self._extract_calls(tree, query, config, file_info, src, symbols)
        heritage = extract_heritage(tree, query, config, file_info, src, run_query=_run_query)
        exports = self._derive_exports(symbols, config, src)
        docstring = extract_module_docstring(root, src, lang)
        type_refs = self._extract_type_refs(tree, query, src)

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
            calls=calls,
            heritage=heritage,
            docstring=docstring,
            parse_errors=parse_errors,
            type_refs=type_refs,
        )

    # ------------------------------------------------------------------
    # Query loading
    # ------------------------------------------------------------------

    def _get_query(self, lang: str, language: Language) -> object | None:
        """Load and cache the compiled tree-sitter Query for *lang*."""
        return _load_compiled_query(lang)

    # ------------------------------------------------------------------
    # Symbol extraction
    # ------------------------------------------------------------------

    def _extract_symbols(
        self,
        tree: object,
        query: object,
        config: LanguageConfig,
        file_info: FileInfo,
        src: str,
    ) -> list[Symbol]:
        if query is None:
            return []

        symbols: list[Symbol] = []
        seen: set[tuple[int, str]] = set()  # (start_line, name) — dedup decorated dupes

        for capture_dict in _run_query(query, tree.root_node):  # type: ignore[attr-defined]
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

            # Refine "struct" kind for Go type_spec (check if struct or interface body)
            if kind == "struct" and config.parent_extraction == "receiver":
                kind = refine_go_type_kind(def_node, src)

            # Refine "class" kind for Kotlin (interface / enum class share class_declaration)
            if kind == "class" and file_info.language == "kotlin" and def_node.type == "class_declaration":
                kind = refine_kotlin_class_kind(def_node)

            # Params signature text
            params_text = _node_text(params_nodes[0], src) if params_nodes else ""

            # Visibility
            modifier_texts = [_node_text(m, src) for m in modifier_nodes]
            if def_node.parent and def_node.parent.type == "decorated_definition":
                for sibling in def_node.parent.children:
                    if sibling.type == "decorator":
                        modifier_texts.append(_node_text(sibling, src))
            visibility = config.visibility_fn(name, modifier_texts)
            is_exported_symbol = False
            # C/C++ visibility is dictated by AST context (access
            # specifiers / storage class / export attributes), not by
            # modifier text. Refine after the generic fn ran.
            if file_info.language in ("cpp", "c"):
                visibility, is_exported_symbol = refine_cpp_visibility(
                    def_node, visibility, src
                )

            # Parent class detection
            parent_name = self._find_parent(def_node, config, receiver_nodes, src)

            # C/C++ qualified definitions: ``void Foo::method() { … }``
            # carries the class as the scope of a ``qualified_identifier``
            # parent of the name node. Without this resolution, every
            # ``Class::method`` lands as a free function and bloats the
            # unused_export pass with thousands of method symbols.
            if (
                parent_name is None
                and file_info.language in ("cpp", "c")
                and name_nodes
            ):
                parent_name = _qualified_cpp_parent(name_nodes[0], src)

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
                    end_line=def_node.end_point[0] + 1,
                    docstring=docstring,
                    decorators=[m for m in modifier_texts if m.startswith("@")],
                    visibility=visibility,  # type: ignore[arg-type]
                    is_async=is_async,
                    language=file_info.language,
                    parent_name=parent_name,
                    is_exported_symbol=is_exported_symbol,
                )
            )

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
                        return _node_text(name_node, src)
                ancestor = ancestor.parent
            return None

        return None  # "none" mode

    # ------------------------------------------------------------------
    # Import extraction
    # ------------------------------------------------------------------

    def _extract_imports(
        self,
        tree: object,
        query: object,
        config: LanguageConfig,
        file_info: FileInfo,
        src: str,
    ) -> list[Import]:
        if query is None:
            return []

        imports: list[Import] = []
        seen_raws: set[str] = set()

        for capture_dict in _run_query(query, tree.root_node):  # type: ignore[attr-defined]
            stmt_nodes = capture_dict.get("import.statement", [])
            module_nodes = capture_dict.get("import.module", [])

            if not stmt_nodes or not module_nodes:
                continue

            stmt_node = stmt_nodes[0]
            raw = _node_text(stmt_node, src).strip()
            if raw in seen_raws:
                continue
            seen_raws.add(raw)

            module_text = _node_text(module_nodes[0], src).strip().strip("\"'` ")
            if not module_text:
                continue

            # Language-specific import name + binding extraction
            imported_names, bindings = extract_import_bindings(stmt_node, src, file_info.language)
            is_relative = module_text.startswith(".") or module_text.startswith("./")

            imports.append(
                Import(
                    raw_statement=raw,
                    module_path=module_text,
                    imported_names=imported_names,
                    is_relative=is_relative,
                    resolved_file=None,
                    bindings=bindings,
                )
            )

        return imports

    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

    def _extract_calls(
        self,
        tree: object,
        query: object,
        config: LanguageConfig,
        file_info: FileInfo,
        src: str,
        symbols: list[Symbol],
    ) -> list[CallSite]:
        """Extract function/method call sites from the AST."""
        if query is None:
            return []

        from .language_data import get_builtin_calls

        _call_builtins = get_builtin_calls(file_info.language)

        symbol_ranges = sorted(
            [(s.start_line, s.end_line, s.id) for s in symbols],
            key=lambda t: (t[0], -t[1]),
        )

        calls: list[CallSite] = []
        seen: set[tuple[int, str, str | None]] = set()

        for capture_dict in _run_query(query, tree.root_node):  # type: ignore[attr-defined]
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
        tree: object,
        query: object,
        src: str,
    ) -> list[TypeReference]:
        """Collect ``@param.type`` captures into TypeReference records.

        Currently only the C# query emits these captures (constructor /
        method / delegate / primary-ctor parameter types). The graph
        builder resolves each reference to a defining file via the
        language-specific resolver index and emits a file-level edge.

        Capture origin is inferred from the parameter's enclosing node:
        ``constructor_declaration`` → ``ctor_param`` (highest signal:
        canonical DI vector), ``method_declaration`` → ``method_param``,
        ``delegate_declaration`` → ``delegate_param``. Primary
        constructors on records / classes also resolve to ``ctor_param``.
        """
        if query is None:
            return []

        refs: list[TypeReference] = []
        seen: set[tuple[str, int]] = set()

        for capture_dict in _run_query(query, tree.root_node):  # type: ignore[attr-defined]
            type_nodes = capture_dict.get("param.type", [])
            if not type_nodes:
                continue
            for type_node in type_nodes:
                head = _head_type_identifier(type_node, src)
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_query(query: object, root_node: Node) -> list[dict[str, list[Node]]]:
    """Execute a tree-sitter query and return a list of capture dicts."""
    results: list[dict[str, list[Node]]] = []
    try:
        from tree_sitter import QueryCursor  # type: ignore[attr-defined]

        cursor = QueryCursor(query)  # type: ignore[call-arg]
        for match in cursor.matches(root_node):
            if hasattr(match, "captures"):
                results.append(match.captures)
            elif isinstance(match, tuple) and len(match) == 2:
                _, caps = match
                results.append(caps)
    except Exception:
        try:
            for item in query.matches(root_node):  # type: ignore[attr-defined]
                if isinstance(item, tuple) and len(item) == 2:
                    _, caps = item
                    results.append(caps)
        except Exception as exc:
            log.warning("query.matches() failed", error=str(exc))
    return results


def _collect_error_nodes(root: Node) -> list[str]:
    """Return error descriptions for any ERROR nodes in the tree."""
    errors: list[str] = []

    def _walk(node: Node) -> None:
        if node.type == "ERROR":
            errors.append(f"Parse error at line {node.start_point[0] + 1}")
        for child in node.children:
            _walk(child)

    _walk(root)
    return errors


def _is_async_node(node: Node, src: str) -> bool:
    return node.type == "async_function_definition" or any(c.type == "async" for c in node.children)


def _qualified_cpp_parent(name_node: Node, src: str) -> str | None:
    """Return the parent class for a C/C++ ``Class::method`` definition.

    The captured ``@symbol.name`` for a qualified function definition
    is the bare ``method`` identifier whose parent is a
    ``qualified_identifier`` carrying the class / namespace as its
    ``scope`` field. For multi-level qualifications (``NS::Foo::method``)
    the relevant parent is still the innermost qualifier — namespaces
    above it are not the symbol's containing type. Tree-sitter-cpp
    represents this by nesting ``qualified_identifier`` left-recursively,
    so the immediate parent's ``scope`` is always the right answer.

    Returns ``None`` when the name node is not inside a qualified
    identifier (i.e. plain free function).
    """
    parent = name_node.parent
    if parent is None or parent.type != "qualified_identifier":
        return None
    scope = parent.child_by_field_name("scope")
    if scope is None:
        return None
    text = src[scope.start_byte : scope.end_byte].strip()
    # ``scope`` may itself be a qualified path (``NS::Foo``); take the
    # last component — that's the immediate enclosing type.
    return text.rsplit("::", 1)[-1] or None


def _build_qualified_name(file_path: str, parent_name: str | None, name: str) -> str:
    module = Path(file_path).with_suffix("").as_posix().replace("/", ".")
    if parent_name:
        return f"{module}.{parent_name}.{name}"
    return f"{module}.{name}"


# ---------------------------------------------------------------------------
# Type reference helpers (used by _extract_type_refs)
# ---------------------------------------------------------------------------

# Type expressions that never resolve to a user-defined .NET type. Skipping
# these here avoids polluting the resolver with hopeless lookups. Generic
# args inside `IList<T>` are stripped before this check is applied.
_BUILTIN_CSHARP_TYPES: frozenset[str] = frozenset({
    "void", "bool", "byte", "sbyte", "char", "short", "ushort", "int",
    "uint", "long", "ulong", "float", "double", "decimal", "string",
    "object", "nint", "nuint", "dynamic", "var",
    # Frequently appearing BCL types that are always external — listing
    # them here is purely a performance optimisation (one dict miss
    # avoided per occurrence).
    "Task", "ValueTask", "CancellationToken", "Action", "Func",
    "Type", "Exception", "DateTime", "DateTimeOffset", "TimeSpan",
    "Guid", "Uri", "Stream",
})

_PARAM_ORIGIN_BY_ANCESTOR: dict[str, str] = {
    "constructor_declaration": "ctor_param",
    "method_declaration": "method_param",
    "delegate_declaration": "delegate_param",
    "record_declaration": "ctor_param",
    "class_declaration": "ctor_param",
    "struct_declaration": "ctor_param",
}


def _head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a C# type expression, or None.

    Examples:
        ``IBasketService``                  → "IBasketService"
        ``IList<Basket>``                   → "IList"
        ``Acme.Catalog.IRepository<T>``     → "IRepository"
        ``ref readonly Span<byte>``         → "Span"
        ``string``                          → None (built-in)
        ``int?``                            → None
        ``T``                               → None (likely a generic param)

    The point of returning the head identifier is that the
    DotNetProjectIndex type-name lookup is keyed by unqualified type
    name. Generic-arg recursion is intentionally NOT done here — each
    generic arg is captured in its own ``@param.type`` if it's a real
    parameter type, and the resolver doesn't currently track generic
    instantiation graphs.
    """
    head_node: Node | None = type_node

    # Unwrap modifier wrappers: nullable_type, ref_type, pointer_type,
    # array_type, tuple_type. tree-sitter-c-sharp puts the inner type
    # at field "type" or as the first non-trivia child.
    for _ in range(6):
        if head_node is None:
            return None
        if head_node.type in ("nullable_type", "ref_type", "pointer_type", "array_type"):
            inner = head_node.child_by_field_name("type")
            if inner is None:
                # Fall back to first identifier-bearing child
                inner = next(
                    (c for c in head_node.children if c.type not in (",", "?", "*", "&", "ref", "out", "in", "[", "]")),
                    None,
                )
            head_node = inner
            continue
        break

    if head_node is None:
        return None

    if head_node.type == "identifier":
        text = _node_text(head_node, src)
    elif head_node.type == "predefined_type":
        text = _node_text(head_node, src)
    elif head_node.type == "generic_name":
        name_child = head_node.child_by_field_name("name") or next(
            (c for c in head_node.children if c.type == "identifier"), None,
        )
        text = _node_text(name_child, src) if name_child else ""
    elif head_node.type == "qualified_name":
        # `Foo.Bar.Baz` — take the rightmost identifier
        idents = [c for c in head_node.children if c.type == "identifier"]
        text = _node_text(idents[-1], src) if idents else ""
    elif head_node.type == "tuple_type":
        return None  # Tuple elements aren't single types
    else:
        # Unknown shape — fall back to first identifier in the subtree
        ident = _first_descendant(head_node, "identifier")
        text = _node_text(ident, src) if ident else ""

    if not text or not text[0].isalpha() and text[0] != "_":
        return None
    if text in _BUILTIN_CSHARP_TYPES:
        return None
    # Single-uppercase-letter heads are overwhelmingly generic params (T, K, V).
    # Skipping them avoids spurious lookups against a type-name index that
    # would never contain them.
    if len(text) == 1 and text.isupper():
        return None
    return text


def _first_descendant(node: Node, type_name: str) -> Node | None:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == type_name:
            return current
        stack.extend(current.children)
    return None


def _classify_param_origin(type_node: Node) -> str:
    """Walk up to find the enclosing declaration and map to an origin tag.

    The walk stops at the first matching ancestor or after a small depth
    cap. Falling off the cap means the capture was outside a recognised
    declaration shape (shouldn't happen given the query patterns, but
    guards against grammar drift); we tag those ``method_param``.
    """
    cur: Node | None = type_node
    for _ in range(8):
        if cur is None:
            break
        origin = _PARAM_ORIGIN_BY_ANCESTOR.get(cur.type)
        if origin is not None:
            return origin
        cur = cur.parent
    return "method_param"


# ---------------------------------------------------------------------------
# Call extraction helpers
# ---------------------------------------------------------------------------


def _count_arguments(arg_node: Node) -> int:
    """Count the number of arguments in an argument/argument_list node."""
    skip_types = frozenset({"(", ")", ",", "[", "]"})
    return sum(1 for child in arg_node.children if child.type not in skip_types)


def _find_enclosing_symbol(
    line: int,
    symbol_ranges: list[tuple[int, int, str]],
) -> str | None:
    """Find the innermost symbol whose line range contains *line*."""
    best_id: str | None = None
    best_span = float("inf")

    for start, end, sym_id in symbol_ranges:
        if start > line:
            break
        if start <= line <= end:
            span = end - start
            if span < best_span:
                best_span = span
                best_id = sym_id

    return best_id
