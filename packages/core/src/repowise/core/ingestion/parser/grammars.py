"""Grammar loading, query compilation and the tree-sitter parse itself."""

from __future__ import annotations

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


def _get_language(tag: str) -> Language | None:
    # Filled in place, never rebound: the package re-exports this dict.
    if not _LANGUAGE_REGISTRY:
        _LANGUAGE_REGISTRY.update(_build_language_registry())
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
