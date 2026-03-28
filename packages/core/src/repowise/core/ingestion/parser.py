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

from .models import FileInfo, Import, ParsedFile, Symbol

log = structlog.get_logger(__name__)

QUERIES_DIR = Path(__file__).parent / "queries"

# Languages that intentionally have no AST parser.  These are data, config,
# markup, or query files — there are no code symbols to extract, and that is
# expected.  parse_file() returns an empty ParsedFile for them silently.
# Keep this list in sync with EXTENSION_TO_LANGUAGE in models.py.
_PASSTHROUGH_LANGUAGES: frozenset[str] = frozenset(
    {
        "json",
        "yaml",
        "toml",
        "markdown",
        "sql",
        "shell",
        "terraform",
        "proto",
        "graphql",
        "dockerfile",
        "makefile",
    }
)

# ---------------------------------------------------------------------------
# Language registry — maps language tag → tree-sitter Language object
# ---------------------------------------------------------------------------


def _build_language_registry() -> dict[str, Language]:
    """Lazily load installed tree-sitter language packages."""
    registry: dict[str, Language] = {}

    def _try_load(tag: str, loader: Callable[[], Language]) -> None:
        try:
            registry[tag] = loader()
        except Exception as exc:  # ImportError, AttributeError, …
            log.debug("tree-sitter language unavailable", language=tag, reason=str(exc))

    _try_load("python", lambda: Language(__import__("tree_sitter_python").language()))

    def _ts() -> None:
        import tree_sitter_typescript as ts

        registry["typescript"] = Language(ts.language_typescript())
        registry["tsx"] = Language(ts.language_tsx())

    try:
        _ts()
    except Exception as exc:
        log.debug("tree-sitter language unavailable", language="typescript", reason=str(exc))

    _try_load("javascript", lambda: Language(__import__("tree_sitter_javascript").language()))
    _try_load("go", lambda: Language(__import__("tree_sitter_go").language()))
    _try_load("rust", lambda: Language(__import__("tree_sitter_rust").language()))
    _try_load("java", lambda: Language(__import__("tree_sitter_java").language()))

    def _cpp() -> None:
        import tree_sitter_cpp as ts_cpp

        lang = Language(ts_cpp.language())
        registry["cpp"] = lang
        registry["c"] = lang  # C is a subset of C++ for our purposes

    try:
        _cpp()
    except Exception as exc:
        log.debug("tree-sitter language unavailable", language="cpp", reason=str(exc))

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


def _py_visibility(name: str, _mods: list[str]) -> str:
    if name.startswith("__") and name.endswith("__"):
        return "public"  # dunder
    if name.startswith("_"):
        return "private"
    return "public"


def _ts_visibility(_name: str, mods: list[str]) -> str:
    mods_lower = [m.lower() for m in mods]
    if "private" in mods_lower:
        return "private"
    if "protected" in mods_lower:
        return "protected"
    return "public"


def _go_visibility(name: str, _mods: list[str]) -> str:
    return "public" if name and name[0].isupper() else "private"


def _rust_visibility(_name: str, mods: list[str]) -> str:
    return "public" if any("pub" in m for m in mods) else "private"


def _java_visibility(_name: str, mods: list[str]) -> str:
    combined = " ".join(mods).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    return "public"


def _public_by_default(_name: str, _mods: list[str]) -> str:
    return "public"


LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {
    "python": LanguageConfig(
        symbol_node_types={
            "function_definition": "function",
            "class_definition": "class",
        },
        import_node_types=["import_statement", "import_from_statement"],
        export_node_types=[],
        visibility_fn=_py_visibility,
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
        visibility_fn=_ts_visibility,
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
        visibility_fn=_public_by_default,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_declaration"}),
        entry_point_patterns=["index.js", "main.js", "app.js", "server.js"],
    ),
    "go": LanguageConfig(
        symbol_node_types={
            "function_declaration": "function",
            "method_declaration": "method",
            "type_spec": "struct",  # refined in post-processing
        },
        import_node_types=["import_declaration"],
        export_node_types=[],
        visibility_fn=_go_visibility,
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
        },
        import_node_types=["use_declaration"],
        export_node_types=[],
        visibility_fn=_rust_visibility,
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
        visibility_fn=_java_visibility,
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
        },
        import_node_types=["preproc_include"],
        export_node_types=[],
        visibility_fn=_public_by_default,
        parent_extraction="nesting",
        parent_class_types=frozenset({"class_specifier", "struct_specifier"}),
        entry_point_patterns=["main.cpp", "main.cc"],
    ),
    "c": LanguageConfig(
        symbol_node_types={
            "function_definition": "function",
            "struct_specifier": "struct",
            "enum_specifier": "enum",
        },
        import_node_types=["preproc_include"],
        export_node_types=[],
        visibility_fn=_public_by_default,
        parent_extraction="none",
        parent_class_types=frozenset(),
        entry_point_patterns=["main.c"],
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
            # If the language has a LANGUAGE_CONFIGS entry but its tree-sitter
            # grammar failed to load, that is unexpected — log it once per file
            # so developers can investigate.  For all other cases (data files,
            # markup, config, languages not yet supported) this is intentional
            # and we return silently without spamming the log.
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
        exports = self._derive_exports(symbols, config, src)
        docstring = _extract_module_docstring(root, src, lang)

        return ParsedFile(
            file_info=file_info,
            symbols=symbols,
            imports=imports,
            exports=exports,
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

        # C files reuse the cpp query
        scm_lang = "cpp" if lang == "c" else lang
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
                kind = _refine_go_type_kind(def_node, src)

            # Params signature text
            params_text = _node_text(params_nodes[0], src) if params_nodes else ""

            # Visibility
            modifier_texts = [_node_text(m, src) for m in modifier_nodes]
            # Also check if parent in decorated_definition has decorators
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
            signature = _build_signature(node_type, name, params_text, def_node, src)

            # Docstring — walk the body of the def_node
            docstring = _extract_symbol_docstring(def_node, src, file_info.language)

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
                return _extract_go_receiver_type(_node_text(receiver_nodes[0], src))
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

            # Language-specific import name extraction
            imported_names = _extract_import_names(stmt_node, src, file_info.language)
            is_relative = module_text.startswith(".") or module_text.startswith("./")

            imports.append(
                Import(
                    raw_statement=raw,
                    module_path=module_text,
                    imported_names=imported_names,
                    is_relative=is_relative,
                    resolved_file=None,
                )
            )

        return imports

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
            # Languages with explicit exports (TS, JS) — public top-level symbols
            return [s.name for s in symbols if s.visibility == "public" and s.parent_name is None]
        # Languages where all top-level public symbols are exported (Python, Go, …)
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
    """Execute a tree-sitter query and return a list of capture dicts.

    Handles both the legacy tuple API and the newer QueryMatch API across
    tree-sitter versions >= 0.22.
    """
    results: list[dict[str, list[Node]]] = []
    try:
        from tree_sitter import QueryCursor  # type: ignore[attr-defined]

        cursor = QueryCursor(query)  # type: ignore[call-arg]
        for match in cursor.matches(root_node):
            if hasattr(match, "captures"):
                # tree-sitter >= 0.23: QueryMatch object
                results.append(match.captures)
            elif isinstance(match, tuple) and len(match) == 2:
                _, caps = match
                results.append(caps)
    except Exception:
        # Fallback: query.matches() returning list of (index, dict) tuples
        try:
            for item in query.matches(root_node):  # type: ignore[attr-defined]
                if isinstance(item, tuple) and len(item) == 2:
                    _, caps = item
                    results.append(caps)
        except Exception as exc:
            log.warning("query.matches() failed", error=str(exc))
    return results


def _node_text(node: Node | None, src: str) -> str:
    if node is None:
        return ""
    if node.text is not None:
        return node.text.decode("utf-8", errors="replace")
    return src[node.start_byte : node.end_byte]


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


def _extract_module_docstring(root: Node, src: str, lang: str) -> str | None:
    """Extract a module/file-level docstring or leading comment."""
    if lang == "python":
        for child in root.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "string":
                        return _clean_string_literal(_node_text(sub, src))
                break
            elif child.type not in (
                "comment",
                "newline",
                "import_statement",
                "import_from_statement",
                "future_import_statement",
            ):
                break
    elif lang in ("typescript", "javascript"):
        # Look for leading /** ... */ comment
        for child in root.children:
            if child.type == "comment":
                text = _node_text(child, src).strip()
                if text.startswith("/**"):
                    return _clean_jsdoc(text)
            elif child.type not in ("comment",):
                break
    elif lang == "go":
        # Package comment is a series of // lines before package_clause
        lines: list[str] = []
        for child in root.children:
            if child.type == "comment":
                lines.append(_node_text(child, src).lstrip("/ ").strip())
            elif child.type == "package_clause":
                break
        return "\n".join(lines) if lines else None
    elif lang == "rust":
        # //! inner doc comments or /// outer doc comments at top
        for child in root.children:
            if child.type in ("line_comment", "block_comment"):
                text = _node_text(child, src).strip()
                if text.startswith("//!") or text.startswith("/*!"):
                    return text.lstrip("/!* ").strip()
            else:
                break
    return None


def _extract_symbol_docstring(def_node: Node, src: str, lang: str) -> str | None:
    """Extract the docstring from a symbol's body node."""
    if lang == "python":
        body = def_node.child_by_field_name("body")
        if body is None:
            return None
        for child in body.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "string":
                        return _clean_string_literal(_node_text(sub, src))
                return None
            elif child.type not in ("comment", "newline"):
                return None
        return None

    elif lang in ("typescript", "javascript"):
        return _find_preceding_jsdoc(def_node, src)

    elif lang == "go":
        # Leading // comment lines before the function
        parent = def_node.parent
        if parent is None:
            return None
        siblings = list(parent.children)
        idx = next((i for i, s in enumerate(siblings) if s.id == def_node.id), -1)
        if idx <= 0:
            return None
        lines: list[str] = []
        i = idx - 1
        while i >= 0 and siblings[i].type == "comment":
            lines.insert(0, _node_text(siblings[i], src).lstrip("/ ").strip())
            i -= 1
        return "\n".join(lines) if lines else None

    elif lang == "rust":
        # /// doc comments before the item
        parent = def_node.parent
        if parent is None:
            return None
        siblings = list(parent.children)
        idx = next((i for i, s in enumerate(siblings) if s.id == def_node.id), -1)
        if idx <= 0:
            return None
        lines: list[str] = []
        i = idx - 1
        while i >= 0 and siblings[i].type in ("line_comment", "block_comment"):
            text = _node_text(siblings[i], src).strip()
            if text.startswith("///"):
                lines.insert(0, text.lstrip("/ ").strip())
                i -= 1
            else:
                break
        return "\n".join(lines) if lines else None

    elif lang == "java":
        # /** Javadoc */ comment before the method/class
        return _find_preceding_block_comment(def_node, src, "/**")

    return None


def _build_signature(node_type: str, name: str, params_text: str, def_node: Node, src: str) -> str:
    """Build a human-readable signature string."""
    if node_type == "function_definition":
        # Detect async via child "async" keyword (tree-sitter-python >= 0.23)
        prefix = "async " if any(c.type == "async" for c in def_node.children) else ""
        # Get return type annotation for Python
        ret_node = def_node.child_by_field_name("return_type")
        ret_text = f" -> {_node_text(ret_node, src)}" if ret_node else ""
        return f"{prefix}def {name}{params_text}{ret_text}"
    if node_type in ("function_declaration", "generator_function_declaration", "function_item"):
        return f"function {name}{params_text}"
    if node_type in ("class_definition", "class_declaration", "abstract_class_declaration"):
        base = f"class {name}"
        if params_text:
            base += params_text
        return base
    if node_type == "interface_declaration":
        return f"interface {name}"
    if node_type == "type_alias_declaration":
        return f"type {name}"
    if node_type == "enum_declaration":
        return f"enum {name}"
    if node_type == "method_definition":
        return f"{name}{params_text}"
    if node_type == "method_declaration":
        return f"func ({name}) method{params_text}"
    if node_type in ("struct_item", "struct_specifier"):
        return f"struct {name}"
    if node_type in ("enum_item", "enum_specifier"):
        return f"enum {name}"
    if node_type == "trait_item":
        return f"trait {name}"
    if node_type == "impl_item":
        return f"impl {name}"
    if node_type in ("class_specifier",):
        return f"class {name}"
    # Fallback
    return f"{name}{params_text}"


def _extract_import_names(stmt_node: Node, src: str, lang: str) -> list[str]:
    """Extract specific imported names from an import statement node."""
    names: list[str] = []

    if lang == "python":
        for child in stmt_node.children:
            if child.type == "wildcard_import":
                return ["*"]
            if child.type == "dotted_name":
                text = _node_text(child, src)
                # Skip the module name itself (it's the first dotted_name in import_from)
                if names or stmt_node.type == "import_statement":
                    names.append(text.split(".")[-1])
                else:
                    names.append(text.split(".")[-1])
            elif child.type == "aliased_import":
                name_child = child.child_by_field_name("name") or (
                    child.children[0] if child.children else None
                )
                if name_child:
                    names.append(_node_text(name_child, src))
        return names

    if lang in ("typescript", "javascript"):
        # Find import_clause → named_imports → import_specifier
        for child in stmt_node.children:
            if child.type == "import_clause":
                for sub in child.children:
                    if sub.type == "identifier":
                        names.append(_node_text(sub, src))  # default import
                    elif sub.type == "named_imports":
                        for spec in sub.children:
                            if spec.type == "import_specifier":
                                name_node = spec.child_by_field_name("name") or (
                                    spec.children[0] if spec.children else None
                                )
                                if name_node:
                                    names.append(_node_text(name_node, src))
                    elif sub.type == "namespace_import":
                        names = ["*"]
        return names

    return []


def _extract_go_receiver_type(receiver_text: str) -> str | None:
    """Extract 'Calculator' from '(c *Calculator)' or '(c Calculator)'."""
    text = receiver_text.strip("() ")
    parts = text.split()
    for part in reversed(parts):
        clean = part.lstrip("*")
        if clean and clean[0].isupper():
            return clean
    return None


def _refine_go_type_kind(type_spec_node: Node, src: str) -> str:
    """Refine the generic 'struct' kind for Go type_spec nodes."""
    type_node = type_spec_node.child_by_field_name("type")
    if type_node is None:
        return "struct"
    type_text = _node_text(type_node, src).strip()
    if type_text.startswith("struct"):
        return "struct"
    if type_text.startswith("interface"):
        return "interface"
    return "type_alias"


def _is_async_node(node: Node, src: str) -> bool:
    return node.type == "async_function_definition" or any(c.type == "async" for c in node.children)


def _clean_string_literal(text: str) -> str:
    text = text.strip()
    for triple in ('"""', "'''"):
        if text.startswith(triple) and text.endswith(triple) and len(text) >= 6:
            return text[3:-3].strip()
    for q in ('"', "'"):
        if text.startswith(q) and text.endswith(q) and len(text) >= 2:
            return text[1:-1].strip()
    return text


def _find_preceding_jsdoc(node: Node, src: str) -> str | None:
    parent = node.parent
    if parent is None:
        return None
    siblings = list(parent.children)
    idx = next((i for i, s in enumerate(siblings) if s.id == node.id), -1)
    if idx <= 0:
        return None
    prev = siblings[idx - 1]
    if prev.type == "comment":
        text = _node_text(prev, src).strip()
        if text.startswith("/**"):
            return _clean_jsdoc(text)
    return None


def _find_preceding_block_comment(node: Node, src: str, prefix: str) -> str | None:
    parent = node.parent
    if parent is None:
        return None
    siblings = list(parent.children)
    idx = next((i for i, s in enumerate(siblings) if s.id == node.id), -1)
    if idx <= 0:
        return None
    prev = siblings[idx - 1]
    if prev.type in ("block_comment", "comment"):
        text = _node_text(prev, src).strip()
        if text.startswith(prefix):
            return _clean_jsdoc(text)
    return None


def _clean_jsdoc(text: str) -> str:
    lines = text.splitlines()
    cleaned: list[str] = []
    for line in lines:
        line = line.strip().lstrip("/*").lstrip()
        if line:
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _build_qualified_name(file_path: str, parent_name: str | None, name: str) -> str:
    module = Path(file_path).with_suffix("").as_posix().replace("/", ".")
    if parent_name:
        return f"{module}.{parent_name}.{name}"
    return f"{module}.{name}"
