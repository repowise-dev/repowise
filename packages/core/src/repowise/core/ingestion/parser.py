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
from .extractors.visibility import (
    csharp_visibility,
    go_visibility,
    java_visibility,
    kotlin_visibility,
    php_visibility,
    public_by_default,
    py_visibility,
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
)

log = structlog.get_logger(__name__)

QUERIES_DIR = Path(__file__).parent / "queries"

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
            "method_declaration": "method",
            "constructor_declaration": "function",
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=java_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset(
            {"class_declaration", "interface_declaration", "enum_declaration"}
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
            "method_declaration": "method",
            "constructor_declaration": "function",
            "property_declaration": "variable",
        },
        import_node_types=["using_directive"],
        export_node_types=[],
        visibility_fn=csharp_visibility,
        parent_extraction="nesting",
        parent_class_types=frozenset(
            {"class_declaration", "interface_declaration", "struct_declaration", "enum_declaration"}
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
        # Cache: lang → compiled Query object (None if .scm not found)
        self._query_cache: dict[str, object] = {}

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
        imports = self._extract_imports(tree, query, config, file_info, src)
        calls = self._extract_calls(tree, query, config, file_info, src, symbols)
        heritage = extract_heritage(tree, query, config, file_info, src, run_query=_run_query)
        exports = self._derive_exports(symbols, config, src)
        docstring = extract_module_docstring(root, src, lang)

        return ParsedFile(
            file_info=file_info,
            symbols=symbols,
            imports=imports,
            exports=exports,
            calls=calls,
            heritage=heritage,
            docstring=docstring,
            parse_errors=parse_errors,
        )

    # ------------------------------------------------------------------
    # Query loading
    # ------------------------------------------------------------------

    def _get_query(self, lang: str, language: Language) -> object | None:
        """Load and cache the compiled tree-sitter Query for *lang*."""
        if lang in self._query_cache:
            return self._query_cache[lang]

        scm_lang = lang
        scm_path = QUERIES_DIR / f"{scm_lang}.scm"

        if not scm_path.exists():
            log.debug("No .scm query file found", language=lang, path=str(scm_path))
            self._query_cache[lang] = None
            return None

        scm_text = scm_path.read_text(encoding="utf-8")
        try:
            from tree_sitter import Query  # type: ignore[attr-defined]

            compiled = Query(language, scm_text)
            self._query_cache[lang] = compiled
            log.debug("Compiled query", language=lang)
            return compiled
        except Exception as exc:
            log.warning("Failed to compile query", language=lang, error=str(exc))
            self._query_cache[lang] = None
            return None

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

            # Parent class detection
            parent_name = self._find_parent(def_node, config, receiver_nodes, src)

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


def _build_qualified_name(file_path: str, parent_name: str | None, name: str) -> str:
    module = Path(file_path).with_suffix("").as_posix().replace("/", ".")
    if parent_name:
        return f"{module}.{parent_name}.{name}"
    return f"{module}.{name}"


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
