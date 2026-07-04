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
from .extractors.bindings.python import expand_bare_relative_imports
from .extractors.synthetic_symbols import extract_synthetic_symbols
from .extractors.visibility import refine_cpp_visibility
from .language_configs import LANGUAGE_CONFIGS, LanguageConfig
from .languages.registry import REGISTRY as _LANG_REGISTRY
from .models import (
    CallSite,
    FileInfo,
    Import,
    ParsedFile,
    Symbol,
    TypeReference,
)
from .parser_helpers import (
    TYPE_HEAD_EXTRACTORS,
    _build_qualified_name,
    _classify_param_origin,
    _collect_error_nodes,
    _count_arguments,
    _find_enclosing_symbol,
    _has_callable_ancestor,
    _head_type_identifier,
    _is_async_node,
    _qualified_cpp_parent,
    _run_query,
)
from .python_local_refs import extract_python_local_refs
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


@lru_cache(maxsize=None)
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

    scm_path = QUERIES_DIR / f"{lang}.scm"
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


# Private alias for internal use (kept for compatibility with _find_parent)
_node_text = node_text


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

        # Non-tree-sitter formats (OpenAPI, Dockerfile, Makefile, SQL) parse
        # via dedicated handlers. Checked before the grammar lookup: none of
        # these tags carry a LanguageConfig, so the no-grammar fallback below
        # would otherwise swallow them.
        if lang in SPECIAL_HANDLER_LANGUAGES:
            return parse_special(file_info, source, lang)

        config = LANGUAGE_CONFIGS.get(lang)
        # .tsx files need the JSX-aware grammar; tree-sitter-typescript's
        # default `language_typescript` errors out on every `<Component />`
        # and the resulting ERROR-node recovery hoists nested helpers
        # (handlers defined inside component bodies) to the top level.
        grammar_tag = "tsx" if lang == "typescript" and file_info.path.endswith(".tsx") else lang
        language = _get_language(grammar_tag)

        if config is None or language is None:
            if config is not None and language is None:
                log.debug(
                    "tree-sitter grammar unavailable",
                    language=lang,
                    path=file_info.path,
                )
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
            )

        parser = Parser(language)
        tree = parser.parse(source)
        src = source.decode("utf-8", errors="replace")
        root = tree.root_node

        parse_errors = _collect_error_nodes(root)
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
        heritage = extract_heritage(matches, config, file_info, src)
        exports = self._derive_exports(symbols, config, src)
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
            calls=calls,
            heritage=heritage,
            docstring=docstring,
            parse_errors=parse_errors,
            type_refs=type_refs,
            local_refs=local_refs,
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

            # Refine module-level assignments: SCREAMING_CASE names are
            # constants by convention; the rest are module variables
            # (singletons like ``app = FastAPI()``, registries, caches).
            # ``str.isupper()`` requires at least one cased char, so names
            # with no letters (``_``, ``__all__``) fall to "variable" rather
            # than being mislabelled constants by ``name == name.upper()``.
            if node_type in _MODULE_ANCHORED_NODE_TYPES:
                kind = "constant" if name.isupper() else "variable"

            # Params signature text
            params_text = _node_text(params_nodes[0], src) if params_nodes else ""

            # Visibility
            modifier_texts = [_node_text(m, src) for m in modifier_nodes]
            if def_node.parent and def_node.parent.type == "decorated_definition":
                for sibling in def_node.parent.children:
                    if sibling.type == "decorator":
                        modifier_texts.append(_node_text(sibling, src))

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

            visibility = config.visibility_fn(name, modifier_texts)
            is_exported_symbol = False
            # C/C++ visibility is dictated by AST context (access
            # specifiers / storage class / export attributes), not by
            # modifier text. Refine after the generic fn ran.
            if file_info.language in ("cpp", "c"):
                visibility, is_exported_symbol = refine_cpp_visibility(def_node, visibility, src)

            # Parent class detection
            parent_name = self._find_parent(def_node, config, receiver_nodes, src)

            # C/C++ qualified definitions: ``void Foo::method() { … }``
            # carries the class as the scope of a ``qualified_identifier``
            # parent of the name node. Without this resolution, every
            # ``Class::method`` lands as a free function and bloats the
            # unused_export pass with thousands of method symbols.
            if parent_name is None and file_info.language in ("cpp", "c") and name_nodes:
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
                    decorators=[m for m in modifier_texts if m.startswith("@")] + rust_attrs,
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

        for capture_dict in matches:
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
