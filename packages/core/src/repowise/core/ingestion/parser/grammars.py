"""Grammar loading, query compilation and the tree-sitter parse itself."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from functools import cache
from pathlib import Path

import structlog
from tree_sitter import Language, Node, Parser

from ..language_configs import LANGUAGE_CONFIGS
from ..languages.registry import REGISTRY as _LANG_REGISTRY
from ..parser_helpers import _collect_error_nodes

# One logger for the whole package, under the package's public name.
log = structlog.get_logger("repowise.core.ingestion.parser")

QUERIES_DIR = Path(__file__).parent.parent / "queries"


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


# Downloads its grammars on first use rather than shipping them in a wheel.
_RUNTIME_DOWNLOAD_PACKAGE = "tree_sitter_language_pack"
# A pre-staged language-pack cache, for machines with no network.
GRAMMAR_DIR_ENV = "REPOWISE_GRAMMAR_DIR"


def _load_language(tag: str) -> Language | None:
    """Load *tag*'s grammar alone, so a repo never loads one it has no files for."""
    if tag == "tsx":
        # TypeScript's tsx variant: same package, different loader.
        try:
            import tree_sitter_typescript as _ts_mod

            return Language(_ts_mod.language_tsx())
        except Exception as exc:
            _GRAMMAR_FAILURES[tag] = str(exc)
            log.debug("tree-sitter language unavailable", language=tag, reason=str(exc))
            return None

    spec = _LANG_REGISTRY.get(tag)
    if spec is None:
        return None
    # Languages that share another's grammar (e.g. C -> cpp).
    if spec.shares_grammar_with:
        return _get_language(spec.shares_grammar_with)
    if not spec.grammar_package:
        return None

    try:
        mod = __import__(spec.grammar_package)
        if spec.grammar_package == _RUNTIME_DOWNLOAD_PACKAGE:
            _configure_grammar_dir(mod)
        loaded = getattr(mod, spec.grammar_loader)(*spec.grammar_loader_args)
        # Standalone grammar wheels return a PyCapsule; shared grammar
        # packs may return the fully constructed Language directly.
        return loaded if isinstance(loaded, Language) else Language(loaded)
    except Exception as exc:
        _GRAMMAR_FAILURES[tag] = str(exc)
        log.debug("tree-sitter language unavailable", language=tag, reason=str(exc))
        return None


def _configure_grammar_dir(pack: object) -> None:
    """Point the language pack at ``$REPOWISE_GRAMMAR_DIR`` when it is set."""
    grammar_dir = os.environ.get(GRAMMAR_DIR_ENV, "").strip()
    if grammar_dir:
        pack.configure(pack.PackConfig(cache_dir=grammar_dir))  # type: ignore[attr-defined]


def downloads_grammar_at_runtime(tag: str) -> bool:
    """Whether *tag*'s grammar comes from the language pack's runtime download."""
    spec = _LANG_REGISTRY.get(tag)
    return spec is not None and spec.grammar_package == _RUNTIME_DOWNLOAD_PACKAGE


def runtime_grammar_cache(tag: str) -> tuple[bool, str]:
    """``(cached, cache_dir)`` for a runtime-downloaded grammar, with no network call."""
    import tree_sitter_language_pack as pack

    _configure_grammar_dir(pack)
    spec = _LANG_REGISTRY.get(tag)
    name = spec.grammar_loader_args[0] if spec and spec.grammar_loader_args else tag
    return name in pack.downloaded_languages(), str(pack.cache_dir())


# Loaded grammars, filled one language at a time by ``_get_language``.
_LANGUAGE_REGISTRY: dict[str, Language] = {}
# Failed tags and why; a failure is not retried per file.
_GRAMMAR_FAILURES: dict[str, str] = {}

# Languages already reported as having a config but no installed grammar:
# one line per language per process, not one per file.
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


def unavailable_runtime_grammars(language_tags: Iterable[str]) -> list[tuple[str, str]]:
    """``(tag, reason)`` for each runtime-downloaded grammar in *language_tags* that fails to load.

    Loaded in the parent, which also fills the cache before the parse workers start.
    """
    failed: list[tuple[str, str]] = []
    for tag in sorted(set(language_tags)):
        if (
            tag in LANGUAGE_CONFIGS
            and downloads_grammar_at_runtime(tag)
            and _get_language(tag) is None
        ):
            failed.append((tag, _GRAMMAR_FAILURES.get(tag, "not loadable")))
    return failed


def _get_language(tag: str) -> Language | None:
    # Filled in place, never rebound: the package re-exports this dict.
    if tag in _LANGUAGE_REGISTRY:
        return _LANGUAGE_REGISTRY[tag]
    if tag in _GRAMMAR_FAILURES:
        return None
    language = _load_language(tag)
    if language is not None:
        _LANGUAGE_REGISTRY[tag] = language
    return language


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


def _parse_tree(
    lang: str, language: Language, grammar_tag: str, source: bytes
) -> tuple[Node, list[str], str, Language]:
    """``(root, parse errors, grammar tag, language)``; a JSX-bearing ``.ts`` retries as TSX."""
    parser = Parser(language)
    tree = parser.parse(source)
    root = tree.root_node

    parse_errors = _collect_error_nodes(root)

    # A .ts file (or a .vue render function) holding JSX errors under the
    # TypeScript grammar; retry as TSX and keep it only when it errors less.
    # ``source`` is the markup-blanked projection, so the token test sees real JSX.
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
                # The caller's query appends tsx.scm for the tsx tag, which
                # supplies the JSX component call sites, so both must change.
                grammar_tag = "tsx"
                language = tsx_language
    return root, parse_errors, grammar_tag, language


# Kotlin modifier words a declaration may carry between its annotations and its
# keyword. tree-sitter-kotlin also accepts most of them as identifiers, which is
# what lets ``private fun f() {}`` read as an infix call.
_KOTLIN_MODIFIER_WORDS = (
    "public|private|internal|protected|abstract|open|final|sealed|data|enum|annotation|"
    "inner|value|inline|noinline|crossinline|tailrec|operator|infix|external|suspend|"
    "override|lateinit|const|vararg|expect|actual|companion|fun"
)
_KOTLIN_DECLARATION_HEAD = re.compile(
    rb"(?:(?:" + _KOTLIN_MODIFIER_WORDS.encode() + rb")\s+)*"
    rb"(?:fun|class|interface|object|val|var)\b"
)


def _kotlin_leading_annotations(top: Node) -> tuple[list[Node], Node | None]:
    """Return annotations leading a top-level expression and the node after them."""
    node = top
    if (
        node.type == "assignment"
        and node.children
        and node.children[0].type == "annotated_expression"
    ):
        node = node.children[0]

    annotations: list[Node] = []
    while node is not None and node.type == "annotated_expression":
        annotations.extend(c for c in node.children if c.type == "annotation")
        node = next(
            (
                c
                for c in node.children
                if c.is_named and c.type not in ("annotation", "line_comment", "block_comment")
            ),
            None,
        )

    return (annotations, node) if annotations and node is not None else ([], None)


def _blank_kotlin_annotations(source: bytes, annotations: list[Node]) -> bytes:
    """Blank annotation bytes while preserving newlines and byte offsets."""
    blanked = bytearray(source)
    for node in annotations:
        for i in range(node.start_byte, node.end_byte):
            if blanked[i] not in (0x0A, 0x0D):
                blanked[i] = 0x20
    return bytes(blanked)


def _recover_kotlin_annotated_declarations(
    language: Language,
    root: Node,
    source: bytes,
) -> tuple[Node, bytes, dict[int, tuple[int, str]]]:
    """Re-parse top-level Kotlin declarations hidden by multi-annotation expressions."""
    found: list[tuple[list[Node], Node]] = []

    for top in root.children:
        if top.type not in ("annotated_expression", "assignment"):
            continue

        annotations, rest = _kotlin_leading_annotations(top)
        if rest is None:
            continue

        head = source[rest.start_byte : rest.start_byte + 160]
        if _KOTLIN_DECLARATION_HEAD.match(head):
            found.append((annotations, rest))

    if not found:
        return root, source, {}

    blanked = _blank_kotlin_annotations(
        source,
        [annotation for annotations, _ in found for annotation in annotations],
    )
    new_root = Parser(language).parse(blanked).root_node

    # Keep the original parse if blanking makes it worse.
    if len(_collect_error_nodes(new_root)) > len(_collect_error_nodes(root)):
        return root, source, {}

    by_start = {child.start_byte: child for child in new_root.children}
    recovered: dict[int, tuple[int, str]] = {}

    for annotations, rest in found:
        first = min(annotations, key=lambda annotation: annotation.start_byte)
        end = max(annotation.end_byte for annotation in annotations)

        decl = by_start.get(rest.start_byte)
        modifiers = (
            next((child for child in decl.children if child.type == "modifiers"), None)
            if decl
            else None
        )
        if modifiers is not None:
            end = max(end, modifiers.end_byte)

        text = source[first.start_byte : end].decode("utf-8", errors="replace")
        recovered[rest.start_point[0] + 1] = (
            first.start_point[0] + 1,
            text,
        )

    return new_root, blanked, recovered
