"""DeadCodeAnalyzer — pure graph + git-metadata dead-code detection.

All analysis is graph traversal + SQL. No LLM calls. Must complete in
< 10 seconds.

The four detection passes (unreachable files, unused exports, unused
internals, zombie packages) live as methods on this class. Constants,
data models, and dynamic-import markers live in sibling modules under
this package.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from ...ingestion.models import REACHABILITY_USE_EDGE_TYPES
from .constants import (
    _CONTAINER_USE_LANGUAGES,
    _DEFAULT_DYNAMIC_PATTERNS,
    _DELIBERATELY_UNUSED_ANNOTATIONS,
    _FRAMEWORK_DECORATOR_SUFFIXES,
    _FRAMEWORK_DECORATORS,
    _NEVER_PACKAGE_DIRS,
    _NON_CODE_LANGUAGES,
    _is_fixture_path,
    never_flag_match,
)
from .contract_methods import is_contract_method
from .dynamic_markers import (
    find_dynamic_edge_files,
    find_dynamic_import_files,
    read_source_text,
)
from .file_reachability import (
    PackageFileMap,
    ReachabilityRescues,
    build_package_file_map,
    is_file_reachable,
)
from .models import DeadCodeFindingData, DeadCodeKind, DeadCodeReport
from .name_occurrences import IDENTIFIER_RE, clamp_unverified_absence
from .risk_factors import (
    RISK_CAP_CONFIDENCE,
    SAFE_CONFIDENCE_THRESHOLD,
    path_risk_factors,
    risk_evidence,
)

#: The unindexed scan below and the repo-wide absence check ask the same
#: question of different file sets, so they share one identifier shape rather
#: than each carrying a copy. See :mod:`name_occurrences`, which owns it.
_IDENTIFIER_RE = IDENTIFIER_RE

#: The confidence ceiling for a finding an unread file could explain is
#: ``RISK_CAP_CONFIDENCE``, imported above rather than redefined: it already
#: means "review candidate, never deletion-ready", it already sits exactly at
#: the default ``min_confidence`` so the finding still surfaces, and the CLI,
#: REST router and MCP tools already read it as the single source of truth. A
#: lower private constant would make these findings vanish from the report,
#: which is the wrong answer for a change whose whole premise is that silent
#: omission is the bug.

#: Read budgets for the unindexed scan. These files are skipped *because* they
#: are large — one corpus repo ships a 104 MB generated parser — so the scan is
#: bounded per file and in total. Tree-sitter costs ~95 MB of RSS per MB of
#: source; this pass is a byte scan and costs far less, but the bound is what
#: keeps it from ever becoming the expensive thing.
_UNINDEXED_SCAN_PER_FILE_BYTES = 4 * 1024 * 1024
_UNINDEXED_SCAN_TOTAL_BYTES = 32 * 1024 * 1024

# ---------------------------------------------------------------------------
# Deprecation detection
# ---------------------------------------------------------------------------

#: Normalised decorator/annotation bases (after stripping leading ``@`` and
#: any call-argument ``(…)`` tail) that signal a symbol is deprecated.
#:
#: Rust inner-attr form (``deprecated``), C# stripped form (``Obsolete``), and
#: C++ stripped form (``deprecated``) are included verbatim because the
#: respective sibling-walk extractors in ``parser.py`` already strip the
#: language-specific bracket pairs before storing.
_DEPRECATED_DECORATOR_BASES: frozenset[str] = frozenset(
    {
        # Python / TypeScript / Scala / Swift
        "deprecated",
        "typing.deprecated",
        "warnings.deprecated",
        # Java / Kotlin (case-sensitive annotation names)
        "Deprecated",
        "kotlin.Deprecated",
        "java.lang.Deprecated",
        # C# (inner attr text after stripping [ ])
        "Obsolete",
        "System.Obsolete",
        "System.ObsoleteAttribute",
    }
)


#: Regex that strips quoted string literals from a decorator/annotation text
#: before splitting on ``@`` boundaries.  Without this,
#: ``@app.route("/deprecated")`` would produce a token whose paren-stripped
#: base is ``app.route`` (correct), but a hypothetical annotation whose
#: *argument* contains ``@something`` would create a spurious token.
_QUOTED_STR_RE: re.Pattern[str] = re.compile(r'"[^"]*"')


def _declared_deliberately_unused(decorators: Iterable[str]) -> bool:
    """True when the author has written that the symbol is deliberately uncalled.

    Distinct from the framework lists, which infer a caller we cannot see. Here
    there may be no caller at all and the annotation says so, which is a
    stronger signal than any heuristic. Read from the raw token because the
    base name is shared with every other use of the same annotation.
    """
    for blob in decorators:
        # A Java/Kotlin modifiers node delivers every annotation concatenated,
        # so the blob is split the way ``_is_symbol_deprecated`` splits it —
        # without this, ``@Named("unused-legacy-bean")`` beside an unrelated
        # ``@SuppressWarnings`` reads as the author's statement.
        for token in str(blob).split("@"):
            if not token.strip():
                continue
            base = _decorator_base(token)
            if any(
                base == name and argument in token
                for name, argument in _DELIBERATELY_UNUSED_ANNOTATIONS
            ):
                return True
    return False


def _decorator_base(raw: str) -> str:
    """Return the normalised base name of a single decorator/annotation token.

    Strips a leading ``@``, trims whitespace, and drops any call-argument
    ``(…)`` tail.  For multi-word tokens (e.g. a Java modifier blob fragment
    ``"Deprecated\n    public"``) only the first whitespace-delimited word is
    kept, so visibility keywords that trail the annotation name are discarded.

    This function handles ONE token.  Callers that receive multi-annotation
    blobs (Java/Kotlin ``modifiers`` node) must split on ``@`` first.
    """
    base = raw.lstrip("@").strip()
    paren = base.find("(")
    if paren >= 0:
        base = base[:paren].strip()
    else:
        # Handle trailing visibility keywords in modifier blobs:
        # "Deprecated\n    public" → "Deprecated"
        parts = base.split()
        base = parts[0] if parts else base
    return base


def _is_framework_registered(decorators: list[str]) -> bool:
    """Whether any decorator wires the symbol into a framework dispatcher.

    Both dead-code passes ask this question, and both asked it with the same
    lines copy-pasted. It lives here once because a registration rule that
    drifts between the export pass and the internals pass is a bug nobody goes
    looking for.

    Three spellings of one fact:

    * a known base name or dotted prefix (``@Component``, ``@app.route``);
    * a known trailing attribute, for a receiver the prefix list cannot
      anticipate because it is named locally (``@my_group.command``);
    * a registration verb in the final path segment. Every suffix entry begins
      with a dot, so that list can only ever see a *dotted* decorator, and
      celery's ``@register_drainer('eventlet')`` is bare — invisible to all of
      them. Reading the final segment covers both spellings at once:
      ``@register``, ``@register_drainer``, ``@Field.register_lookup``. Held to
      ``register`` exactly or a ``register_`` stem so that a past participle
      guarding a handler (``@registered_only``) is not read as one.
    """
    bases = [_decorator_base(d) for d in decorators]
    if any(b.startswith(_FRAMEWORK_DECORATORS) for b in bases):
        return True
    if any(b.endswith(_FRAMEWORK_DECORATOR_SUFFIXES) for b in bases):
        return True
    return any(
        segment == "register" or segment.startswith("register_")
        for segment in (b.rsplit(".", 1)[-1] for b in bases)
    )


def _is_symbol_deprecated(sym_name: str, decorators: list[str]) -> bool:
    """Return True when the symbol is marked deprecated by name suffix or annotation.

    Two mechanisms are checked in order:

    1. **Name suffix**: the name ends with ``_DEPRECATED``, ``_LEGACY``, or
       ``_COMPAT`` (original naming-convention check, preserved for backward
       compatibility).

    2. **Decorator / annotation**: each entry in *decorators* is treated as a
       possibly multi-annotation modifier blob (Java/Kotlin ``modifiers`` node
       delivers all annotations concatenated with whitespace in a single
       string).  Each entry is first cleaned of quoted substrings (to avoid
       matching paths like ``"/deprecated"`` inside ``@app.route("/deprecated")``),
       then split on ``@`` boundaries so every individual annotation is checked
       independently.  Each token is normalised via :func:`_decorator_base` and
       tested against ``_DEPRECATED_DECORATOR_BASES`` and the lower-cased
       ``"deprecated"`` catch-all.

    The *decorators* list is produced by ``parser.py`` ``_extract_symbols``:
    - Python / Scala / Swift: full decorator text with leading ``@``
      (e.g. ``"@deprecated"``, ``"@typing.deprecated"``).
    - Java / Kotlin: full modifier-node text — one blob per declaration that
      may contain several annotations plus visibility keywords
      (e.g. ``"@Deprecated\n    public"``,
      ``"@Override\n  @Deprecated\n  public"``).
    - Rust: inner attribute content stripped of ``#[…]``
      (e.g. ``"deprecated"`` from ``#[deprecated]``).
    - C#: inner attribute content stripped of ``[…]``
      (e.g. ``"Obsolete"`` from ``[Obsolete]``).
    - C++: inner attribute content stripped of ``[[…]]``
      (e.g. ``"deprecated"`` from ``[[deprecated]]``).
    """
    # 1. Name suffix
    if any(sym_name.endswith(s) for s in ("_DEPRECATED", "_LEGACY", "_COMPAT")):
        return True
    # 2. Decorator / annotation
    for raw in decorators:
        # Strip quoted strings first so path arguments like "/deprecated"
        # inside ``@app.route("/deprecated")`` do not create false tokens.
        cleaned = _QUOTED_STR_RE.sub('""', raw)
        # Split on '@' to tokenize modifier blobs.  A leading '@' produces
        # an empty first element which the ``if not token`` guard discards.
        # No-'@' forms (Rust/C#/C++ inner attrs: "deprecated", "Obsolete")
        # yield a single token equal to the whole string.
        for token in cleaned.split("@"):
            token = token.strip()
            if not token:
                continue
            base = _decorator_base(token)
            if base in _DEPRECATED_DECORATOR_BASES or base.lower() == "deprecated":
                return True
    return False

# Symbol kinds that cannot be independently imported by name in any
# supported language. Flagging them as "unused exports" is a guaranteed
# false-positive — they're always accessed through an enclosing class /
# namespace. C# auto-properties land in the graph as ``variable``;
# fields / enum members / type aliases / namespace anchors share the
# same property.
_UNIVERSAL_NON_IMPORTABLE: frozenset[str] = frozenset(
    {
        "method",
        "variable",
        "field",
        "property",
        "enum_member",
        "constant",
        "type_alias",
        "namespace",
        "module",
    }
)

# Additional kinds skipped only for languages where the graph cannot yet
# observe interface usage. In practice these are DI-heavy languages
# whose canonical interface-consumption path is constructor injection —
# resolved by the type-use edge pass (see
# ``ingestion/type_ref_resolution.py``). Once a language emits
# ``via=type_use`` edges, its entry here can be removed.
#
# C# already has type-use coverage (ctor + method + delegate +
# primary-ctor param.type captures), so ``interface`` is *not* skipped
# for C# — a genuinely unused C# interface is now observable.
#
# TS / Python / JS interfaces were always imported by name and never
# needed the skip; treating them uniformly produced false negatives.
_LANGUAGE_NON_IMPORTABLE: dict[str, frozenset[str]] = {
    "java": frozenset({"interface"}),
    "kotlin": frozenset({"interface"}),
    "scala": frozenset({"interface"}),
}


# Kinds allowed as top-level imports in TS/JS (top-level `export const` literals/objects)
_TS_JS_IMPORTABLE_KINDS: frozenset[str] = frozenset({"constant", "variable"})

# Single-file-component languages: the whole file is one component, so their
# exports behave unlike an ordinary module's — see _non_importable_kinds.
_SFC_LANGUAGES: frozenset[str] = frozenset({"svelte", "vue"})

# Every kind an SFC component prop can take, plus "class" for the synthetic
# component symbol itself — see _non_importable_kinds.
_SFC_NON_IMPORTABLE_KINDS: frozenset[str] = frozenset(
    {"constant", "variable", "function", "class", "interface", "type_alias"}
)


def _non_importable_kinds(language: str) -> frozenset[str]:
    """Per-language set of symbol kinds excluded from unused-export passes.

    Returns the union of the universal set and any language-specific
    additions. Cheap to call — short lookup, no per-call allocation
    when the language has no additions.
    """
    # An SFC's top-level exports are its props: Svelte's ``export let x`` is
    # set by the PARENT as a markup attribute (``<Foo x={1} />``), never by an
    # ``import { x }``. repowise models component instantiation as a call edge
    # on the component, not as symbol-level edges on each prop, so every prop
    # would read as an unused export.
    #
    # The component symbol itself needs the same treatment, which is why
    # "class" is in the set. A component is reached as a whole module — by a
    # markup tag, or by a router's ``import('@/views/profile')``, which binds
    # no name at all — so it never carries the symbol-level inbound edge the
    # unused-export pass looks for. On vue-element-admin that alone accounted
    # for 45 of 53 findings, every one of them a false positive.
    #
    # Suppressing the whole pass is the honest ceiling: it costs the
    # genuinely-dead named exports of a Svelte ``<script context="module">``
    # or a Vue non-setup ``<script>``, which cannot be told apart from props
    # without modelling attribute-to-prop binding across files.
    if language in _SFC_LANGUAGES:
        return _UNIVERSAL_NON_IMPORTABLE | _SFC_NON_IMPORTABLE_KINDS

    extra = _LANGUAGE_NON_IMPORTABLE.get(language)
    if extra is None:
        if language in ("typescript", "javascript"):
            return _UNIVERSAL_NON_IMPORTABLE - _TS_JS_IMPORTABLE_KINDS
        return _UNIVERSAL_NON_IMPORTABLE
    return _UNIVERSAL_NON_IMPORTABLE | extra



# Preserved for tests / external callers that imported the old name.
# New code should prefer ``_non_importable_kinds(language)``.
_NON_IMPORTABLE_SYMBOL_KINDS: frozenset[str] = _UNIVERSAL_NON_IMPORTABLE | frozenset({"interface"})

# Aggregate *type* kinds that are never call targets. The unused-internal
# pass is a call-graph check ("private symbol with no incoming CALL edges"),
# which is meaningful for functions/methods but not for types: a struct or
# interface used only as a field/parameter/return type — especially within
# its own file — has no call edge and no observable symbol-level type edge,
# so "no callers" is not evidence of deadness. Such types are still subject
# to the *unused-export* pass (which reasons over import names / type_use),
# so genuinely-dead exported types are still surfaced there.
_UNCALLABLE_TYPE_KINDS: frozenset[str] = frozenset(
    {
        "struct",
        "interface",
        "enum",
        "type_alias",
    }
)


#: C/C++ symbol kinds a bare ``class Env;`` / ``struct Options;`` can carry.
#: Paired with ``is_declaration`` this identifies a type forward declaration,
#: which is never a deletable unit — see the guard in ``_detect_unused_exports``.
_CPP_TYPE_DECLARATION_KINDS: frozenset[str] = frozenset(
    {
        "class",
        "struct",
        "enum",
    }
)

# Symbol names that are language-runtime entry points or compiler-implicit
# anchors — never invoked by user-authored callers, never dead.
_ENTRY_POINT_SYMBOL_NAMES: frozenset[str] = frozenset(
    {
        "Main",  # C#, Java, Kotlin, Go, Rust, Swift, Scala
        "main",  # most others
        # ---- Go runtime / test conventions ------------------------------
        # ``func init`` is run by the Go runtime when the package is linked —
        # never called by name, so it has no inbound call edge. ``TestMain``
        # is the test-binary entry the ``go test`` runner invokes by reflection.
        "init",
        "TestMain",
        "MauiProgram",  # .NET MAUI
        "Program",  # C# top-level / classic console
        "Startup",  # ASP.NET Core legacy
        "__module__",  # synthetic per-file module anchor
        "_start",  # C runtime
        # ---- Python WSGI / ASGI / app-factory conventions ---------------
        # Loaded by external servers (uvicorn / gunicorn / hypercorn /
        # Tornado / aiohttp / Django) via dotted-path string such as
        # ``module:create_app`` or ``module:application``. The graph never
        # sees a call edge from the launching server, so without this
        # allowlist every web entry point shows up as an unused public
        # symbol with 1.0 confidence.
        "create_app",
        "make_app",
        "create_application",
        "make_application",
        "application",
        "asgi_app",
        "wsgi_app",
        "asgi_application",
        "wsgi_application",
        "get_asgi_application",
        "get_wsgi_application",
        # ---- Windows DLL / COM entry points -----------------------------
        # Invoked by the Windows loader or COM runtime; never referenced
        # statically from user code.
        "DllMain",
        "DllGetClassObject",
        "DllCanUnloadNow",
        "DllRegisterServer",
        "DllUnregisterServer",
        "DllGetActivationFactory",
        "DllInstall",  # legacy MSI custom-action entry
        # ---- Win32 GUI / console entry points ---------------------------
        "wWinMain",  # Unicode WinMain
        "WinMain",  # ANSI WinMain
        "wmain",  # Unicode console main
        "ServiceMain",  # Win32 service entry
        # ---- libFuzzer / Honggfuzz / AFL fuzz harness entries ------------
        # The fuzzer driver invokes these by name via dlsym; no static
        # caller will ever exist.
        "LLVMFuzzerTestOneInput",
        "LLVMFuzzerInitialize",
        # ---- Windows hook / ETW callbacks invoked by macros / runtime ----
        "LowLevelKeyboardProc",
        "LowLevelMouseProc",
        "RegisterProvider",  # ETW provider registration (macro-invoked)
        # ---- MSTest unit-test macro --------------------------------------
        # ``TEST_METHOD(Name)`` expands into a public static function with
        # ``TEST_METHOD`` as the captured symbol name; the runner finds it
        # by attribute reflection. Same shape on every C++ unit test file.
        "TEST_METHOD",
        "TEST_CLASS",
        "TEST_METHOD_INITIALIZE",
        "TEST_METHOD_CLEANUP",
        "TEST_CLASS_INITIALIZE",
        "TEST_CLASS_CLEANUP",
        "BEGIN_TEST_METHOD_PROPERTIES",
        "END_TEST_METHOD_PROPERTIES",
        # ---- Next.js (app + pages router) convention exports -------------
        # Loaded by the Next.js runtime by name; never appear as user-code
        # imports. The convention file globs already cover ``page.tsx``/
        # ``route.ts``/``layout.tsx``, so this set only catches the long
        # tail of route exports that escape file-glob protection (e.g.
        # routes placed in non-standard paths). Limited to names that are
        # distinctive enough not to risk masking dead code in unrelated
        # files; common identifiers (``load``, ``action``, ``metadata``,
        # ``config``, ``headers``, ``meta``, ``links``, ``runtime``) are
        # deliberately omitted — they get file-level protection via the
        # convention globs in :data:`_NEVER_FLAG_PATTERNS`.
        "generateStaticParams",
        "generateMetadata",
        "generateViewport",
        "generateImageMetadata",
        "generateSitemaps",
        "dynamicParams",
        "fetchCache",
        "preferredRegion",
        "maxDuration",
        "getStaticProps",
        "getStaticPaths",
        "getServerSideProps",
        "getInitialProps",
        "reportWebVitals",
        # ---- Remix route module exports (distinctive names only) ---------
        "shouldRevalidate",
        "ErrorBoundary",
        "CatchBoundary",
        "HydrateFallback",
        "clientLoader",
        "clientAction",
        # ---- SvelteKit page/layout module exports (distinctive names) ----
        "trailingSlash",
        # ---- JVM (Java + Kotlin) runtime / serialization / contract anchors ----
        # ``main`` is already covered above; these are the rest of the names
        # the JVM resolves through reflection / serialization / Lombok-equivalent
        # generation, never through static call edges. ``INSTANCE`` is the
        # Kotlin ``object Foo`` singleton field; the JVM accesses it directly.
        "serialVersionUID",
        "readObject",
        "writeObject",
        "readObjectNoData",
        "readResolve",
        "writeReplace",
        "canEqual",  # Lombok-equivalent generated method
        "INSTANCE",  # Kotlin object singleton field
        "Companion",  # Kotlin companion-object accessor
    }
)


# Compiler-intrinsic preprocessor macros C/C++ libraries redefine as a
# fallback for compilers that don't ship them (``#if !defined(__has_include)
# \n#define __has_include(h) 0``). The tree-sitter cpp grammar extracts the
# ``#define`` as a ``preproc_function_def`` symbol, but the call sites are
# preprocessor ``#if __has_include(...)`` directives, which the static
# graph cannot observe — so without this skip every such fallback flags
# as an unused export.
_CPP_BUILTIN_MACROS: frozenset[str] = frozenset(
    {
        "__has_include",
        "__has_include_next",
        "__has_feature",
        "__has_extension",
        "__has_attribute",
        "__has_cpp_attribute",
        "__has_c_attribute",
        "__has_declspec_attribute",
        "__has_builtin",
        "__has_warning",
        "__builtin_expect",
        "__builtin_unreachable",
        "__builtin_assume",
        "__builtin_constant_p",
        "__is_identifier",
        "__FILE__",
        "__LINE__",
        "__DATE__",
        "__TIME__",
        "__func__",
        "__FUNCTION__",
        "__PRETTY_FUNCTION__",
    }
)

logger = structlog.get_logger(__name__)


def _find_jsx_namespace_files(
    parsed_files: dict,
    source_map: dict[str, bytes] | None = None,
) -> set[str]:
    """Return repo-relative paths of TS/TSX files that declare ``namespace JSX``.

    Symbols whose name is in :data:`_TS_JSX_NAMESPACE_TYPES` and whose
    defining file lives in this set are integration points with the JSX
    transformer — referenced implicitly by every JSX expression, never
    imported by name. The scan is a cheap substring check; tree-sitter
    grammar work for a richer signal would be wasted effort.
    """
    matches: set[str] = set()
    for path, pf in parsed_files.items():
        try:
            file_info = getattr(pf, "file_info", None)
            if file_info is None:
                continue
            src_path = Path(file_info.abs_path)
            if src_path.suffix not in (".ts", ".tsx", ".d.ts"):
                continue
            source = read_source_text(path, file_info.abs_path, source_map)
            # Match ``namespace JSX`` and ``declare namespace JSX`` — both
            # are JSX transformer integration points in practice.
            if "namespace JSX" in source:
                matches.add(path)
        except Exception:
            continue
    return matches


_BUNDLER_CONFIG_STEMS = ("vite.config", "webpack.config", "rollup.config", "rspack.config")

# Any quoted slash-containing path-ish string. Bundler configs reference
# alias targets both as ``'./src/shims/x.ts'`` and as bare segments fed to
# ``path.resolve(here, 'src/shims/x.ts')``; the parsed-files membership
# probe below keeps loose matches harmless.
_RELATIVE_PATH_STRING_RE = re.compile(r"""['"]((?:\.\.?/)?[\w@.-]+(?:/[\w@.-]+)+)['"]""")

_TS_PROBE_SUFFIXES = ("", ".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs")


def _find_bundler_alias_targets(
    parsed_files: dict,
    source_map: dict[str, bytes] | None = None,
) -> set[str]:
    """Repo-relative paths referenced from bundler config files.

    Vite/webpack ``resolve.alias`` entries substitute a bare package import
    with a local file (``shiki`` → ``src/shims/shiki.ts``): no source file
    ever imports the shim path, only the config names it, so the shim reads
    as unreachable. Any relative path string inside a bundler config that
    resolves to an indexed file marks that file reachable.
    """
    targets: set[str] = set()
    configs = [
        (path, pf)
        for path, pf in parsed_files.items()
        if Path(path).name.startswith(_BUNDLER_CONFIG_STEMS)
    ]
    for path, pf in configs:
        try:
            file_info = getattr(pf, "file_info", None)
            if file_info is None:
                continue
            source = read_source_text(path, file_info.abs_path, source_map)
        except Exception:
            continue
        config_dir = Path(path).parent
        for match in _RELATIVE_PATH_STRING_RE.finditer(source):
            base = _posix_normpath(config_dir, match.group(1))
            for suffix in _TS_PROBE_SUFFIXES:
                candidate = base + suffix
                if candidate in parsed_files:
                    targets.add(candidate)
                    break
    return targets


def _posix_normpath(config_dir: Path, raw: str) -> str:
    """Join *raw* onto *config_dir* and normalise ``.``/``..`` as POSIX."""
    import posixpath

    return posixpath.normpath(posixpath.join(config_dir.as_posix(), raw))


def _find_ts_export_aliases(parsed_files: dict) -> dict[str, dict[str, str]]:
    """Per-file ``{local_name: exported_alias}`` maps for TS/JS alias exports.

    ``export { ConversationHistoryWrapper as ConversationHistory }`` publishes
    the symbol under the alias, so importers pull ``ConversationHistory`` and
    the local name never appears in any ``imported_names`` edge. The unused-
    export pass consults this map to match the alias as well.

    Inverted from what the parser already recorded, rather than scanned again.
    This pass used to own a second regex over the same clause and a second read
    of every TS/JS file; the parser now reads the clause once, with comments
    stripped, and the two answers can no longer disagree.
    """
    aliases: dict[str, dict[str, str]] = {}
    for path, pf in parsed_files.items():
        published = getattr(pf, "export_aliases", None)
        if not published:
            continue
        aliases[path] = {local: exported for exported, local in published.items()}
    return aliases


def _is_synthetic_node(node: str) -> bool:
    """True for non-file graph nodes that should be skipped in 'is this dead?' passes.

    Two synthetic prefixes exist:
      - ``external:`` — third-party / unresolved imports.
      - ``framework:`` — anchors added by ``framework_edges`` to model
        convention-based loading (e.g. TYPO3 core loading ``ext_localconf.php``).

    Both are skipped when the analyzer asks "is this node itself dead?",
    but they are treated differently in the zombie-package pass: ``framework:``
    predecessors *do* count as cross-package importers (real framework-
    mediated dependencies), whereas ``external:`` predecessors do not.
    """
    return node.startswith("external:") or node.startswith("framework:")


class DeadCodeAnalyzer:
    """Detects unreachable files, unused exports, unused internals, and
    zombie packages using the dependency graph and git metadata.
    """

    def __init__(
        self,
        graph: Any,  # nx.DiGraph
        git_meta_map: dict | None = None,
        parsed_files: dict | None = None,
        source_map: dict[str, bytes] | None = None,
        repo_root: Path | None = None,
        unindexed_source_files: list[tuple[str, str]] | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}
        # Source files ingestion could not read (currently: dropped on size).
        # An unread importer is invisible to the graph, so "no importers" stops
        # meaning "unused" and starts meaning "we did not look" — which is how
        # one skipped entry point turned into 38 deletion-ready components at
        # high confidence (#1237). ``(path, reason)`` pairs; the reason rides
        # along so the evidence line can say why.
        self._unindexed_source_files = list(unindexed_source_files or [])
        self._repo_root = repo_root
        self._unindexed_tokens: frozenset[str] | None = None
        # Kept for the absence check below, which is the one pass that needs
        # the raw text of *every* indexed file rather than of a suffix-filtered
        # subset. Empty when a caller has no source access, which is what makes
        # that check skip rather than guess.
        self._source_map: dict[str, bytes] = source_map or {}
        # Three prepasses below each scan every indexed file for text markers.
        # ``source_map`` is ingestion's ``{repo_relative_path: raw bytes}`` for
        # the same file set, so passing it turns three full-repo disk passes
        # into dict lookups. Callers that don't have it (a resume view, the
        # standalone ``dead-code`` command on a graph built elsewhere) pass
        # None and each prepass reads from disk exactly as before.
        #
        # The export-alias map was a fourth and is no longer a pass at all:
        # the parser records the clause, so it is a dict inversion.
        self._dynamic_import_files = find_dynamic_import_files(
            parsed_files or {}, source_map
        ) | find_dynamic_edge_files(graph)
        # Parent directories of the above, precomputed once.
        #
        # ``_make_unreachable_finding`` asks "does any dynamic-import file sit
        # in the same package as this node". That used to be a scan over
        # ``_dynamic_import_files`` constructing a ``Path`` per candidate, run
        # once per unreachable candidate: O(candidates x dynamic_files) with a
        # pathlib constant. A set of parent strings makes it one hash lookup,
        # so the pass is linear in candidates again. On a 30k-file JS monorepo
        # (8.8k files carrying dynamic-import markers) that took the dead-code
        # phase from 374.9s to 10.1s with byte-identical findings.
        #
        # Derived state: this is only valid for the current
        # ``_dynamic_import_files``. Treat both as immutable after __init__,
        # or update them together.
        self._dynamic_import_dirs: set[str] = {
            str(Path(dif).parent) for dif in self._dynamic_import_files
        }
        self._jsx_namespace_files: set[str] = _find_jsx_namespace_files(
            parsed_files or {}, source_map
        )
        # Files substituted in via bundler ``resolve.alias`` config — named
        # by the config, never imported by path.
        self._bundler_alias_targets: set[str] = _find_bundler_alias_targets(
            parsed_files or {}, source_map
        )
        # ``export { local as alias }`` maps so importer edges carrying the
        # alias still count for the local symbol.
        self._ts_export_aliases: dict[str, dict[str, str]] = _find_ts_export_aliases(
            parsed_files or {}
        )
        # Lazily-built package-directory maps for Go / JVM / C-C++, the one
        # piece of the rescue state that costs a graph scan. Built on first use
        # so a graph that never reaches the unreachable-files pass never pays
        # for it. Cached here rather than on the ``ReachabilityRescues`` object
        # because the whitelist arrives per ``analyze()`` call and the map does
        # not.
        self._package_files: PackageFileMap | None = None

    def _reachability_rescues(self, whitelist: AbstractSet[str]) -> ReachabilityRescues:
        """Assemble the rescue state the shared predicate reads."""
        if self._package_files is None:
            self._package_files = build_package_file_map(self.graph)
        return ReachabilityRescues(
            bundler_alias_targets=frozenset(self._bundler_alias_targets),
            whitelist=frozenset(whitelist),
            package_files=self._package_files,
        )

    def analyze(
        self,
        config: dict | None = None,
        *,
        on_step: Any | None = None,
    ) -> DeadCodeReport:
        """Full analysis. Returns report with all findings.

        *on_step* is an optional callable invoked with a stage name after
        each detector finishes (``unreachable_files``, ``unused_exports``,
        ``unused_internals``, ``zombie_packages``). Used by the CLI to
        advance per-stage progress; safe to pass ``None``.
        """
        cfg = config or {}
        findings: list[DeadCodeFindingData] = []

        dynamic_patterns = cfg.get("dynamic_patterns", _DEFAULT_DYNAMIC_PATTERNS)
        whitelist = set(cfg.get("whitelist", []))

        if cfg.get("detect_unreachable_files", True):
            findings.extend(self._detect_unreachable_files(dynamic_patterns, whitelist))
            if on_step:
                on_step("unreachable_files")

        if cfg.get("detect_unused_exports", True):
            findings.extend(self._detect_unused_exports(dynamic_patterns, whitelist))
            if on_step:
                on_step("unused_exports")

        if cfg.get("detect_unused_internals", True):
            findings.extend(self._detect_unused_internals(dynamic_patterns, whitelist))
            if on_step:
                on_step("unused_internals")

        if cfg.get("detect_zombie_packages", True):
            findings.extend(self._detect_zombie_packages(whitelist))
            if on_step:
                on_step("zombie_packages")

        # Before the confidence filter, not after: a finding an unread importer
        # could explain must be able to fall *below* min_confidence and drop
        # out entirely, rather than being reported at a number it no longer
        # deserves.
        findings = self._clamp_for_unindexed_importers(findings)
        # Same position and for the same reason. This one asks the wider
        # version of the same question — not "could an unread file explain
        # this" but "did we look anywhere except the import graph".
        findings = clamp_unverified_absence(findings, self._source_map)

        min_conf = cfg.get("min_confidence", RISK_CAP_CONFIDENCE)
        hidden_below_threshold = sum(1 for f in findings if f.confidence < min_conf)
        findings = [f for f in findings if f.confidence >= min_conf]

        now = datetime.now(UTC)
        deletable = sum(f.lines for f in findings if f.safe_to_delete)

        high = sum(1 for f in findings if f.confidence >= SAFE_CONFIDENCE_THRESHOLD)
        medium = sum(
            1
            for f in findings
            if RISK_CAP_CONFIDENCE <= f.confidence < SAFE_CONFIDENCE_THRESHOLD
        )
        low = sum(1 for f in findings if f.confidence < RISK_CAP_CONFIDENCE)

        return DeadCodeReport(
            repo_id="",
            analyzed_at=now,
            total_findings=len(findings),
            findings=findings,
            deletable_lines=deletable,
            confidence_summary={"high": high, "medium": medium, "low": low},
            hidden_below_threshold=hidden_below_threshold,
        )

    # ------------------------------------------------------------------
    # Detection methods
    # ------------------------------------------------------------------

    def _detect_unreachable_files(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect files nothing can reach that are not tests, fixtures, or config."""
        findings = []

        # One object for the whole pass rather than one per node: the rescues
        # do not vary by candidate.
        rescues = self._reachability_rescues(whitelist)

        for node in self.graph.nodes():
            if _is_synthetic_node(str(node)):
                continue

            node_data = self.graph.nodes[node]
            # The three skips left here are *scoping*, not reachability: a test
            # file is perfectly reachable, it is just not something this pass
            # reports. Everything that answers "can anything get to this file"
            # — entry points, API contracts, never-flag globs, the whitelist,
            # barrels, bundler-alias shims and the package-granular languages
            # (Go / JVM / C-C++) — lives in the predicate, which the overview
            # assembler calls with the same state so the two cannot disagree.
            # See :mod:`file_reachability`.
            if node_data.get("language", "unknown") in _NON_CODE_LANGUAGES:
                continue
            if node_data.get("is_test", False):
                continue
            if _is_fixture_path(str(node)):
                continue

            if is_file_reachable(str(node), self.graph, rescues):
                continue

            finding = self._make_unreachable_finding(str(node), node_data, dynamic_patterns)
            if finding:
                findings.append(finding)

        return findings

    def _unindexed_identifier_tokens(self) -> frozenset[str]:
        """Identifiers appearing in the source files ingestion never read.

        Suppressing every finding whenever any file was skipped would gut the
        feature on a repo with one vendored bundle. Reading the skipped files
        once and asking "is this symbol even mentioned there" keeps the
        suppression to the findings an unread importer could actually explain.

        Bounded on purpose. Files skipped on size are skipped *because* they
        are large, and one corpus repo ships a 104 MB generated parser; files
        skipped for having no language spec are individually small but arrive
        in the hundreds. A partial read can only under-suppress (a missed
        mention leaves the finding as it was), which is the safe direction to
        be wrong in.
        """
        if self._unindexed_tokens is not None:
            return self._unindexed_tokens
        if not self._unindexed_source_files or self._repo_root is None:
            self._unindexed_tokens = frozenset()
            return self._unindexed_tokens

        tokens: set[str] = set()
        budget = _UNINDEXED_SCAN_TOTAL_BYTES
        for rel_path, reason in self._unindexed_source_files:
            # A minified bundle is the one skipped file that must NOT feed this
            # set. It is machine-packed and carries its whole dependency tree
            # inlined, so a single 700 KB vendor bundle contributes tens of
            # thousands of identifiers and would clamp most of the report.
            if reason == "minified":
                continue
            if budget <= 0:
                break
            try:
                with open(self._repo_root / rel_path, "rb") as handle:
                    blob = handle.read(min(_UNINDEXED_SCAN_PER_FILE_BYTES, budget))
            except OSError:
                continue
            budget -= len(blob)
            # ``finditer`` rather than ``findall``: the latter materialises
            # every match at once, roughly 500k bytes objects for a 4 MB blob.
            tokens.update(m.group().decode("ascii", "ignore") for m in _IDENTIFIER_RE.finditer(blob))
        self._unindexed_tokens = frozenset(tokens)
        return self._unindexed_tokens

    def _clamp_for_unindexed_importers(
        self, findings: list[DeadCodeFindingData]
    ) -> list[DeadCodeFindingData]:
        """Downgrade findings an unread file could explain.

        Mutates in place and returns the same list. Never raises a finding's
        confidence and never deletes one: a reader still sees the candidate,
        but it stops claiming to be deletion-ready on evidence we do not have.
        """
        if not self._unindexed_source_files:
            return findings
        tokens = self._unindexed_identifier_tokens()
        if not tokens:
            return findings

        skipped_names = ", ".join(path for path, _ in self._unindexed_source_files[:3])
        if len(self._unindexed_source_files) > 3:
            skipped_names += f" (+{len(self._unindexed_source_files) - 3} more)"

        for finding in findings:
            # Symbol findings only. A whole-file finding would have to match on
            # the path stem, and stems are exactly the broad words ("index",
            # "main", "utils", "app") that ``risk_factors`` deliberately keeps
            # out of its own token map because they cap ordinary modules. An
            # unread importer imports symbols, so match on symbols.
            #
            # Exported symbols only, for the same reason stated the other way:
            # an importer can only reach what a file exports, so a match on an
            # internal one is a name collision rather than evidence. That is
            # not hypothetical — a published API dump lists a public `add`, and
            # every unrelated private `add` in the repo would clamp with it.
            if finding.kind is DeadCodeKind.UNUSED_INTERNAL:
                continue
            name = finding.symbol_name
            if not name or name not in tokens:
                continue
            finding.confidence = min(finding.confidence, RISK_CAP_CONFIDENCE)
            finding.safe_to_delete = False
            finding.evidence.append(
                f"'{name}' appears in a file that was not indexed "
                f"({skipped_names}), so its references could not be checked"
            )
        return findings

    def _make_unreachable_finding(
        self,
        node: str,
        node_data: dict,
        dynamic_patterns: tuple[str, ...],
    ) -> DeadCodeFindingData | None:
        """Create an unreachable file finding with confidence scoring."""
        git_meta = self.git_meta_map.get(node, {})
        commit_90d = git_meta.get("commit_count_90d", 0)
        last_commit = git_meta.get("last_commit_at")
        age_days = git_meta.get("age_days")
        primary_owner = git_meta.get("primary_owner_name")

        # _is_old uses strict >, so pass days-1 to get >= semantics.
        if commit_90d == 0 and last_commit and self._is_old(last_commit, days=364):
            confidence = 1.0  # Untouched for a year+ — very likely dead
        elif commit_90d == 0 and last_commit and self._is_old(last_commit, days=179):
            confidence = 0.9
        elif commit_90d == 0 and last_commit and self._is_old(last_commit, days=89):
            confidence = 0.8
        elif commit_90d == 0 and age_days is not None and age_days < 30:
            confidence = 0.55  # Recently created — may be WIP
        elif commit_90d == 0:
            confidence = 0.7
        else:
            confidence = 0.4

        # The ladder above is an evidence scale, not a tier boundary: its rungs
        # stay literal so moving a threshold does not silently re-score how
        # strong the git signal is. Everything below compares against or caps
        # to a threshold, so it reads the constants.

        # Reduce confidence when dynamic imports exist in the same package.
        if self._dynamic_import_dirs and str(Path(node).parent) in self._dynamic_import_dirs:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        # Runtime-load risk factors (config / bootstrap / database /
        # environment / script / asset). These are files the never-flag allowlist
        # didn't catch but that are commonly referenced outside static
        # imports, so "in_degree=0" is weak evidence. Cap confidence below the
        # deletion-ready threshold and surface the factors as evidence — the
        # finding still shows up as a review candidate.
        risk_factors = path_risk_factors(node)
        if risk_factors:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        safe = confidence >= SAFE_CONFIDENCE_THRESHOLD
        if safe and self._matches_dynamic_patterns(node, dynamic_patterns):
            safe = False

        evidence = ["in_degree=0 (no files import this)"]
        if commit_90d == 0:
            evidence.append("No commits in last 90 days")
        if self._dynamic_import_files and confidence <= RISK_CAP_CONFIDENCE:
            evidence.append("Package uses dynamic imports or runtime-resolved edges")
        risk_line = risk_evidence(risk_factors)
        if risk_line:
            evidence.append(risk_line)

        return DeadCodeFindingData(
            kind=DeadCodeKind.UNREACHABLE_FILE,
            file_path=node,
            symbol_name=None,
            symbol_kind=None,
            confidence=confidence,
            reason="File has no importers (in_degree=0)",
            last_commit_at=last_commit if isinstance(last_commit, datetime) else None,
            commit_count_90d=commit_90d,
            lines=node_data.get("symbol_count", 0) * 10,  # rough estimate
            evidence=evidence,
            safe_to_delete=safe,
            primary_owner=primary_owner,
            age_days=age_days,
            risk_factors=list(risk_factors),
        )

    def _member_is_used(self, sym_id: str, language: str | None) -> bool:
        """True when this container declares a method something else uses.

        Reads the ``has_method`` edges the graph already holds, so this adds no
        pass and no extraction.

        Two narrowings, and both were forced by review rather than chosen:

        **The use must come from outside the container.** A class's own methods
        are predecessors of each other, so counting them would make any class
        with two methods where one calls the other rescue itself — evidence
        about the inside of a container is not evidence that anything reaches
        it.

        **Only a call counts, not the wider reachability set.** Transferring
        member-level evidence to the container is only sound when something
        actually invokes the member; an ``implements`` or ``method_implements``
        edge says the member satisfies a contract, which is a fact about the
        member's shape and not about anyone reaching the class.
        """
        if language not in _CONTAINER_USE_LANGUAGES or not self.graph.has_node(sym_id):
            return False
        members = {
            method_id
            for _, method_id, data in self.graph.out_edges(sym_id, data=True)
            if data.get("edge_type") == "has_method"
        }
        within = members | {sym_id}
        for method_id in members:
            if any(
                pred not in within and self.graph[pred][method_id].get("edge_type") == "calls"
                for pred in self.graph.predecessors(method_id)
            ):
                return True
        return False

    def _detect_unused_exports(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect public symbols with no incoming edges."""
        findings = []

        for node in self.graph.nodes():
            if _is_synthetic_node(str(node)):
                continue

            node_data = self.graph.nodes[node]
            if node_data.get("language", "unknown") in _NON_CODE_LANGUAGES:
                continue
            # Framework-instantiated files (Spring stereotypes, JAX-RS
            # resources, Quarkus components, Spring Data repos, …) have
            # no source-level caller; the runtime constructs them via
            # classpath scanning, so an ``@RestController`` class must not be
            # reported as an unused export.
            #
            # Deliberately not ``is_file_reachable``, which the sibling
            # unreachable-files pass uses: this pass asks about *symbols*, and
            # the predicate's barrel rescue is scoped to files on purpose — a
            # genuine symbol defined in a barrel nobody imports should still be
            # flagged. See ``BARREL_FILENAMES``'s scope note.
            if node_data.get("is_entry_point", False):
                continue
            if node_data.get("is_test", False):
                continue
            if _is_fixture_path(str(node)):
                continue
            if self._should_never_flag(str(node), whitelist):
                continue

            # Pair each symbol's data with its node id so we can check
            # incoming ``calls`` edges on the symbol itself further down.
            symbol_pairs = [
                (succ, self.graph.nodes[succ])
                for succ in self.graph.successors(node)
                if self.graph.nodes[succ].get("node_type") == "symbol"
                and self.graph.get_edge_data(node, succ, {}).get("edge_type") == "defines"
            ]
            if not symbol_pairs:
                continue
            symbols = [sym for _, sym in symbol_pairs]

            file_has_importers = self.graph.in_degree(node) > 0

            # Dispatch-table / namespace-import rescue at the file level:
            # if any importer pulled this file by its module name
            # (``from . import cargo``, ``import * as cargo from
            # "./cargo"``), every public symbol in the file is reachable
            # via ``cargo.<attr>`` and we cannot tell statically which
            # attribute is being called. Treat all public symbols as live.
            # Generic across Python and TS/JS — no repo-specific assumptions.
            #
            # Excluded for Go: every Go import names the *package*, and a
            # file commonly shares its package's name (``dynacache.go`` in
            # package ``dynacache``), which would blanket-rescue every public
            # symbol in such files. Go package-qualified calls are now
            # resolved precisely (call_resolver._resolve_go_package_call), so
            # the imprecise namespace rescue is both unnecessary and harmful
            # here — it would hide genuinely dead exports.
            file_stem = Path(str(node)).stem
            file_imported_as_namespace = False
            if (
                file_stem
                and file_stem not in ("__init__", "index")
                and node_data.get("language") != "go"
            ):
                for pred in self.graph.predecessors(node):
                    edge = self.graph.get_edge_data(pred, node, {})
                    if edge.get("edge_type") != "imports":
                        continue
                    imported = edge.get("imported_names", [])
                    if file_stem in imported:
                        file_imported_as_namespace = True
                        break

            # Dynamic-use edges (DI registration, reflection, event bus
            # subscriptions, framework-mediated loading) target a file
            # as a whole — the runtime resolves the class and reaches
            # any public member. Treat the whole file as live so we
            # don't flag e.g. ``BasketService`` (registered via
            # ``MapGrpcService<BasketService>()``) as an unused export.
            # Was ("dynamic_uses", "dynamic", "framework"): the bare "dynamic"
            # matched nothing and dynamic_imports was absent, so a file reached
            # only by a dynamic import was never rescued here.
            # Deliberately NOT `is_dynamic_edge`: `dynamic_imports` and
            # `dynamic_url_route` mean the module gets loaded, which is what a
            # plain `imports` edge means, and that is not rescued here either.
            # Only `dynamic_uses` carries "the runtime reached a member".
            # Widening this to every dynamic_* hides an unused export in any
            # package.json `main` target or Django INSTALLED_APPS module.
            file_dynamically_loaded = any(
                self.graph.get_edge_data(pred, node, {}).get("edge_type")
                in ("dynamic_uses", "framework")
                for pred in self.graph.predecessors(node)
            )
            if file_dynamically_loaded:
                continue

            # Bundler ``resolve.alias`` shim: the whole module is substituted
            # for a package at build time — every public symbol is reachable
            # through the aliased import.
            if str(node) in self._bundler_alias_targets:
                continue

            # Function/method line ranges in this file — used to skip symbols
            # whose definition is nested inside another function (closures,
            # inner helpers).  Such symbols are only reachable from their
            # enclosing scope and are guaranteed false positives.
            enclosing_ranges = [
                (sym.get("start_line", 0), sym.get("end_line", 0))
                for sym in symbols
                if sym.get("kind") in ("function", "method", "async_function")
                and sym.get("end_line", 0) > sym.get("start_line", 0)
            ]

            for sym_id, sym in symbol_pairs:
                if sym.get("visibility") != "public":
                    continue
                sym_name = sym.get("name", "")

                # Skip symbol kinds that can't be independently imported
                # (methods, properties, fields, enum members, namespace
                # anchors). They're always reached through their enclosing
                # class / module, so the unused-export pass can't observe
                # their real usage and would report guaranteed false
                # positives. C# auto-properties surface here as ``variable``.
                if sym.get("kind") in _non_importable_kinds(sym.get("language", "unknown")):
                    continue
                # Types declared inside a ``namespace JSX`` block are
                # integration points with the JSX transformer — referenced
                # implicitly by every JSX expression, never imported by
                # name. The tree-sitter extractor doesn't carry namespace
                # parentage through to ``parent_name``, so the file-level
                # ``namespace JSX`` source-scan is the working signal we
                # have. Names like ``IntrinsicElements`` /
                # ``ElementChildrenAttribute`` carry the canonical TS
                # JSX-protocol meaning; anything else inside such a file
                # is an HTML-attribute / CSS-property shape consumed by
                # the same machinery.
                if (
                    sym.get("kind") in ("interface", "type_alias")
                    and str(node) in self._jsx_namespace_files
                ):
                    continue
                if sym_name.startswith("__") and sym_name.endswith("__"):
                    continue
                if sym_name in _ENTRY_POINT_SYMBOL_NAMES:
                    continue
                # VS Code extension lifecycle: the host calls ``activate`` /
                # ``deactivate`` on the ``main`` module (conventionally
                # ``extension.ts``) — no in-repo importer ever names them.
                if sym_name in ("activate", "deactivate") and Path(str(node)).stem == "extension":
                    continue
                # Compiler-builtin macros defined as a fallback
                # (``#if !defined(__has_include)\n#define __has_include(h) 0``).
                # The tree-sitter cpp grammar emits the ``#define`` as a
                # ``preproc_function_def`` symbol, but the name is a
                # compiler intrinsic — there will never be a static caller
                # because the real call sites are preprocessor
                # ``#if __has_include(...)`` directives, not C/C++ calls.
                if sym.get("language") in ("cpp", "c") and sym_name in _CPP_BUILTIN_MACROS:
                    continue
                # Rust proc-macro entry points — invoked by the compiler,
                # not by call edges in the dependency graph.
                if sym.get("language") == "rust":
                    decorators = sym.get("decorators") or []
                    if any(d.startswith("proc_macro") for d in decorators):
                        continue
                # Explicit language-level export markers (C/C++
                # ``__declspec(dllexport)``, GCC ``visibility("default")``)
                # signal "called from outside this translation unit /
                # binary" — never observable in the static graph.
                if sym.get("is_exported_symbol"):
                    continue
                # A C/C++ forward declaration whose definition was found is not
                # independently deletable — the definition is the unit of
                # deletion, and call resolution attaches the use edge there
                # rather than to the header line, so reporting the declaration
                # too would only restate what the definition says (#1601). A
                # prototype with no definition anywhere is the opposite case:
                # nothing else can carry the finding, so it still gets one.
                if sym.get("is_declaration") and sym.get("defined_by"):
                    continue
                # A C/C++ *type* forward declaration is not a deletable unit at
                # all, paired or not, so it is not held to the clause above.
                # A prototype promises a body, and a body that exists nowhere
                # makes the prototype itself the dead thing. ``class Env;``
                # promises nothing: it exists so the declaring file can name
                # the type without including its header, which makes that file
                # the declaration's user. Deleting the line breaks it whether
                # the definition lives in this repo or in a dependency — and
                # when it is in the repo, the definition already carries the
                # finding.
                if (
                    sym.get("is_declaration")
                    and sym.get("language") in ("cpp", "c")
                    and sym.get("kind") in _CPP_TYPE_DECLARATION_KINDS
                ):
                    continue
                # Names that contain a dot are namespace path fragments
                # (e.g. ``eShop.ClientApp``), not user-visible exports.
                if "." in sym_name:
                    continue

                # Skip nested defs: a symbol whose start_line falls strictly
                # inside another function/method's body cannot be imported
                # by name from outside the enclosing scope.
                sym_start = sym.get("start_line", 0)
                if any(
                    start < sym_start < end
                    for start, end in enclosing_ranges
                    if (start, end) != (sym_start, sym.get("end_line", 0))
                ):
                    continue

                decorators = sym.get("decorators", [])

                if _is_framework_registered(decorators):
                    continue
                if _declared_deliberately_unused(decorators):
                    continue

                if self._name_matches_dynamic(sym_name, dynamic_patterns):
                    continue

                # Same-file type-position usage rescue (TS/JS): the
                # type-ref strategy stamps ``local_type_uses`` on a file
                # node with every type name referenced inside its own
                # source — parameter / field / return / heritage /
                # generic-constraint / type-alias-RHS positions. An
                # ``interface DefaultRenderer`` consumed only as a
                # ``type Renderer = ... : DefaultRenderer`` annotation in
                # the same module is genuinely live; without this rescue
                # the whole class of intra-module type protocols (Hono's
                # ``Get``/``Set`` generics, AWS Lambda's per-adapter
                # event-shape interfaces) reads as dead exports.
                local_type_uses = node_data.get("local_type_uses")
                if local_type_uses and sym_name in local_type_uses:
                    continue

                # Same-file reference rescue (Python): a top-level function
                # or class consumed only within its own module in a non-call
                # position carries no graph edge — passed as a first-class
                # callable argument (``_score_dimension(.., weight_fn, ..)``),
                # used purely as a type annotation (a Pydantic model that is
                # only a FastAPI request-body param type), named in a
                # decorator, or stored as a default/collection value. The
                # parser stamps these intra-module references on the file node
                # (see ``ingestion/python_local_refs.py``); treat them as live.
                local_refs = node_data.get("local_refs")
                if local_refs and sym_name in local_refs:
                    continue

                is_deprecated = _is_symbol_deprecated(
                    sym_name, sym.get("decorators") or []
                )

                # ``export { local as alias }`` publishes the symbol under the
                # alias; importers carry the alias in ``imported_names``.
                export_alias = self._ts_export_aliases.get(str(node), {}).get(sym_name)

                has_importers = False
                for pred in self.graph.predecessors(node):
                    edge_data = self.graph[pred][node]
                    imported_names = edge_data.get("imported_names", [])
                    if (
                        sym_name in imported_names
                        or "*" in imported_names
                        or (export_alias is not None and export_alias in imported_names)
                    ):
                        has_importers = True
                        break

                if has_importers:
                    continue

                # Namespace-import rescue: see ``file_imported_as_namespace``
                # computation above. Any public symbol in a file pulled by
                # module name could be the dispatch target for
                # ``<modname>.<attr>(...)``.
                if file_imported_as_namespace:
                    continue

                # Symbol-level usage signal: any incoming ``calls`` /
                # ``method_implements`` / ``reads`` / ``extends`` /
                # ``implements`` / ``type_use`` edge means somewhere in
                # the codebase actually uses this symbol — even if the
                # file-level ``imported_names`` machinery missed it
                # (intra-file C++ helpers, ``Foo::method`` qualified
                # definitions linked by call resolution but never named
                # in a header, Razor/XAML code-behind dispatches, and
                # abstract base classes / interfaces that are only ever
                # extended or implemented, never called directly — Java
                # padding bases like ``BoundedLocalCache.BLCHeader``,
                # Kotlin sealed parents, Scala typeclass traits).
                if self.graph.has_node(sym_id) and any(
                    self.graph[pred][sym_id].get("edge_type") in REACHABILITY_USE_EDGE_TYPES
                    for pred in self.graph.predecessors(sym_id)
                ):
                    continue

                # A container whose member is used is itself used, and no
                # search for the container's own name can see it. A C# static
                # holder class is only ever named at its declaration --
                # ``Guard.Against.EmptyBasket(...)`` writes the method, never
                # ``BasketGuards`` -- so the name really is absent and the name
                # is the wrong thing to look for. Asked after the direct check
                # so it can only rescue, never displace.
                #
                # Gated to C# because that is where the idiom lives. The
                # argument generalises -- deleting any class whose method has a
                # caller breaks that caller -- but ungating it removes findings
                # in every language at once, which is its own measured change.
                if self._member_is_used(sym_id, sym.get("language")):
                    continue

                if is_deprecated:
                    confidence = 0.3
                elif file_has_importers:
                    confidence = 1.0
                else:
                    confidence = 0.7

                # Interfaces / protocols are reached almost exclusively
                # through their implementors. Implementor detection is
                # heuristic — its absence is "evidence missing", not
                # "evidence of absence". Cap confidence below the
                # safe-to-delete threshold when the file containing the
                # interface has no incoming ``implements``-class edges,
                # so the demo doesn't ship public-API interfaces as
                # confident dead code. Generic across all languages
                # (C#, Java, Kotlin, Scala, Swift protocols, TS).
                if sym.get("kind") == "interface" and not self._file_has_implementors(node):
                    confidence = min(confidence, RISK_CAP_CONFIDENCE)

                # COM / IUnknown / IDispatch contract methods
                # (``QueryInterface``, ``AddRef``, ``Release``, …) are
                # dispatched through native vtables — no static caller
                # edge will ever land in the graph. Clamp below the
                # safe-to-delete threshold so we never ship them as
                # confident dead code on Windows / COM-heavy C++ repos.
                if is_contract_method(sym_name, sym.get("kind"), sym.get("language", "unknown")):
                    confidence = min(confidence, RISK_CAP_CONFIDENCE)

                # Runtime-load risk factors for the defining file (config /
                # bootstrap / database / environment / script / asset): symbols in
                # such files are often wired up reflectively, so cap below the
                # deletion-ready threshold and tag the finding for review.
                risk_factors = path_risk_factors(str(node))
                if risk_factors:
                    confidence = min(confidence, RISK_CAP_CONFIDENCE)

                safe = confidence >= SAFE_CONFIDENCE_THRESHOLD

                git_meta = self.git_meta_map.get(str(node), {})

                evidence = [f"No imports of '{sym_name}' found in graph"]
                risk_line = risk_evidence(risk_factors)
                if risk_line:
                    evidence.append(risk_line)

                findings.append(
                    DeadCodeFindingData(
                        kind=DeadCodeKind.UNUSED_EXPORT,
                        file_path=str(node),
                        symbol_name=sym_name,
                        symbol_kind=sym.get("kind"),
                        confidence=confidence,
                        reason=f"Public symbol '{sym_name}' has no importers",
                        last_commit_at=git_meta.get("last_commit_at")
                        if isinstance(git_meta.get("last_commit_at"), datetime)
                        else None,
                        commit_count_90d=git_meta.get("commit_count_90d", 0),
                        lines=sym.get("end_line", 0) - sym.get("start_line", 0),
                        # Both-or-neither: a half-known span is worse than none.
                        start_line=(sym.get("start_line") or None) if sym.get("end_line") else None,
                        end_line=(sym.get("end_line") or None) if sym.get("start_line") else None,
                        evidence=evidence,
                        safe_to_delete=safe,
                        primary_owner=git_meta.get("primary_owner_name"),
                        age_days=git_meta.get("age_days"),
                        risk_factors=list(risk_factors),
                    )
                )

        return findings

    def _detect_unused_internals(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect private symbols with zero incoming call edges.

        On by default. These carry a higher false-positive rate than the other
        detectors, which is why they land at a lower confidence. Disable with
        ``detect_unused_internals=False`` in the config dict.
        """
        findings: list[DeadCodeFindingData] = []

        for node, node_data in self.graph.nodes(data=True):
            if node_data.get("node_type") != "symbol":
                continue
            # Rust: the graph builder does not yet emit intra-file call
            # edges, so every private Rust function appears "uncalled".
            # Skip the entire language until call-edge support lands.
            if node_data.get("language") == "rust":
                continue
            # Go's call resolver now resolves same-package (sibling-file) and
            # package-qualified calls (see call_resolver._resolve_go_*), so
            # private symbols used across a package's files carry real
            # ``calls`` edges and no longer read as universally uncalled. The
            # blanket exemption that Phase 2 added has been lifted.
            # ``internal`` is not narrow: assembly-wide in C#, module-wide in
            # Swift and Kotlin, crate-wide in Rust. A legitimate user can sit
            # anywhere in the module, so a missing inbound call edge is not
            # evidence of deadness. Nothing else observes ``internal`` either,
            # which drops an unmodified C# top-level type out of both passes.
            if node_data.get("visibility") != "private":
                continue
            file_path = node_data.get("file_path", "")
            if not file_path:
                continue
            file_data = self.graph.nodes.get(file_path, {})
            if file_data.get("is_test", False):
                continue
            if _is_fixture_path(file_path):
                continue
            if self._should_never_flag(file_path, whitelist):
                continue

            sym_name = node_data.get("name", "")
            if sym_name.startswith("__") and sym_name.endswith("__"):
                continue
            if sym_name in _ENTRY_POINT_SYMBOL_NAMES:
                continue
            # Namespace-path fragments (e.g. ``eShop.ClientApp``) and
            # non-callable kinds bypass the call-edge pass by design.
            if "." in sym_name:
                continue
            if node_data.get("kind") in _non_importable_kinds(node_data.get("language", "unknown")):
                continue
            # Non-callable type kinds can't have CALL edges; the call-graph
            # check this pass performs is meaningless for them (see
            # _UNCALLABLE_TYPE_KINDS). They remain covered by unused_export.
            if node_data.get("kind") in _UNCALLABLE_TYPE_KINDS:
                continue
            if is_contract_method(
                sym_name, node_data.get("kind"), node_data.get("language", "unknown")
            ):
                continue
            if self._name_matches_dynamic(sym_name, dynamic_patterns):
                continue

            # Framework-decorator skip — same shape as unused-export. A
            # private ``@PostConstruct``/``@EventListener``/``@Scheduled``
            # method is invoked by the container, not by a source call.
            decorators = node_data.get("decorators") or []

            if _is_framework_registered(decorators):
                continue
            if _declared_deliberately_unused(decorators):
                continue

            # Any inbound use, not only a call. A base class that is subclassed
            # rather than instantiated, a collaborator the container constructs,
            # and a handler named as a value rather than invoked each carry a
            # symbol-level edge of their own; reading only ``calls`` here
            # reported all three as unused. The set is shared with the
            # unused-export pass, so it also carries types this population can
            # never hold — a method or an interface is filtered out above.
            is_used = any(
                self.graph.get_edge_data(pred, node, {}).get("edge_type")
                in REACHABILITY_USE_EDGE_TYPES
                for pred in self.graph.predecessors(node)
            )
            if is_used:
                continue

            # Dispatch-table pattern: a private helper imported by name
            # into a sibling module and stored in a lookup dict
            # (``HANDLERS = {"python": _extract_python_heritage, ...}``).
            # The function is reached at runtime via dict lookup, so no
            # direct ``calls`` edge ever lands in the graph — but the
            # ``imports`` edge into its file carries the symbol name. If
            # any cross-file importer pulled this symbol by name,
            # something is actively referencing it; do not flag.
            file_pred_imports = False
            for pred in self.graph.predecessors(file_path):
                edge = self.graph.get_edge_data(pred, file_path, {})
                if edge.get("edge_type") != "imports":
                    continue
                imported = edge.get("imported_names", [])
                if sym_name in imported or "*" in imported:
                    file_pred_imports = True
                    break
            if file_pred_imports:
                continue

            git_meta = self.git_meta_map.get(file_path, {})
            # Private symbols keep the standard 0.65 base confidence even if deprecated.
            # A private symbol has no external consumer by construction —
            # deprecated + uncalled is the strongest possible delete signal and
            # must not be buried below the default min_confidence floor.
            # (0.3 is reserved for unused *exports*, where an invisible consumer
            # outside the repo may still import it.)
            findings.append(
                DeadCodeFindingData(
                    kind=DeadCodeKind.UNUSED_INTERNAL,
                    file_path=file_path,
                    symbol_name=sym_name,
                    symbol_kind=node_data.get("kind"),
                    confidence=0.65,
                    reason=f"Private symbol '{sym_name}' is not used anywhere",
                    last_commit_at=git_meta.get("last_commit_at")
                    if isinstance(git_meta.get("last_commit_at"), datetime)
                    else None,
                    commit_count_90d=git_meta.get("commit_count_90d", 0),
                    lines=node_data.get("end_line", 0) - node_data.get("start_line", 0),
                    # Both-or-neither: a half-known span is worse than none.
                    start_line=(node_data.get("start_line") or None)
                    if node_data.get("end_line")
                    else None,
                    end_line=(node_data.get("end_line") or None)
                    if node_data.get("start_line")
                    else None,
                    evidence=[f"No call, reference or override reaches '{sym_name}'"],
                    safe_to_delete=False,
                    primary_owner=git_meta.get("primary_owner_name"),
                    age_days=git_meta.get("age_days"),
                    risk_factors=list(path_risk_factors(file_path)),
                )
            )

        return findings

    def _detect_zombie_packages(self, whitelist: set[str]) -> list[DeadCodeFindingData]:
        """Detect monorepo packages with no incoming inter_package edges.

        ``framework:`` predecessors (synthetic anchors added by
        ``framework_edges``) count as cross-package importers — TYPO3 / Django
        / etc. wiring is a real cross-cutting dependency. ``external:``
        predecessors do not count (they represent third-party imports).
        """
        findings = []

        packages: dict[str, list[str]] = {}
        for node in self.graph.nodes():
            if _is_synthetic_node(str(node)):
                continue
            parts = Path(str(node)).parts
            if len(parts) > 1:
                pkg = parts[0]
                packages.setdefault(pkg, []).append(str(node))

        if len(packages) < 2:
            return findings

        for pkg, files in packages.items():
            if pkg in whitelist:
                continue
            # Skip known non-package dirs (.github, .vscode, docs, ...)
            # and any other dotfile directory at the repo root.
            if pkg in _NEVER_PACKAGE_DIRS or pkg.startswith("."):
                continue
            # A real package contains at least one source-code file. If
            # every file under the candidate dir is config/data (YAML,
            # JSON, MD, TOML), it is not a package — it is metadata.
            has_code_file = any(
                self.graph.nodes.get(f, {}).get("language", "unknown") not in _NON_CODE_LANGUAGES
                for f in files
            )
            if not has_code_file:
                continue

            has_external_importers = False
            for f in files:
                for pred in self.graph.predecessors(f):
                    pred_str = str(pred)
                    if pred_str.startswith("external:"):
                        # Third-party imports don't count as cross-package
                        # importers; framework: synthetic anchors do.
                        continue
                    pred_parts = Path(pred_str).parts
                    if len(pred_parts) > 0 and pred_parts[0] != pkg:
                        has_external_importers = True
                        break
                if has_external_importers:
                    break

            if not has_external_importers:
                total_lines = sum(
                    self.graph.nodes[f].get("symbol_count", 0) * 10
                    for f in files
                    if f in self.graph
                )
                pkg_last_commit: datetime | None = None
                pkg_total_commits_90d = 0
                pkg_owner: str | None = None
                owner_counts: dict[str, int] = {}
                for f in files:
                    # git_meta_map values are plain dicts (see pipeline/phases/analysis.py);
                    # use .get() not getattr() — getattr on a dict never finds arbitrary
                    # string keys and always returns the default, silently zeroing all
                    # git-activity metadata on zombie-package findings.
                    gm = self.git_meta_map.get(f)
                    if gm is None:
                        continue
                    f_last = gm.get("last_commit_at")
                    if f_last and (pkg_last_commit is None or f_last > pkg_last_commit):
                        pkg_last_commit = f_last
                    pkg_total_commits_90d += gm.get("commit_count_90d", 0) or 0
                    f_owner = gm.get("primary_owner_name")
                    if f_owner:
                        owner_counts[f_owner] = owner_counts.get(f_owner, 0) + 1
                if owner_counts:
                    pkg_owner = max(owner_counts, key=lambda k: owner_counts[k])
                pkg_age_days: int | None = None
                if pkg_last_commit:
                    pkg_age_days = (datetime.now(UTC) - pkg_last_commit).days

                findings.append(
                    DeadCodeFindingData(
                        kind=DeadCodeKind.ZOMBIE_PACKAGE,
                        file_path=pkg,
                        symbol_name=None,
                        symbol_kind=None,
                        confidence=0.5,
                        reason=f"Package '{pkg}' has no importers from other packages",
                        last_commit_at=pkg_last_commit,
                        commit_count_90d=pkg_total_commits_90d,
                        lines=total_lines,
                        evidence=[f"No inter-package imports into '{pkg}'"],
                        safe_to_delete=False,
                        primary_owner=pkg_owner,
                        age_days=pkg_age_days,
                        risk_factors=list(path_risk_factors(pkg)),
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_never_flag(self, path: str, whitelist: set[str]) -> bool:
        """Return True if path should never be flagged as dead.

        Used by the unused-export, unused-internal and zombie-package passes.
        The unreachable-files pass does not call it: every limb below is also
        asked by :func:`is_file_reachable`, which that pass calls anyway, and
        two spellings of one rule is what this phase exists to remove.
        """
        if path in whitelist:
            return True
        if never_flag_match(path):
            return True
        # Workspace-driven never-flag — set by language warmups that read
        # the build manifest (Gradle non-``main`` source sets, Cargo
        # ``[[example]]`` / ``[[bench]]`` targets, …). Lets each language
        # learn conventions from its own build files instead of us
        # extending the hardcoded glob list every time a repo defines a
        # custom source set like ``testFixtures`` / ``javaPoet`` /
        # ``jcstress``.
        node = self.graph.nodes.get(path)
        if node is not None and node.get("is_never_flag", False):
            return True
        # __init__.py is a re-export barrel
        return Path(path).name == "__init__.py"

    def _file_has_implementors(self, file_node: Any) -> bool:
        """Return True iff any ``implements`` / ``method_implements`` /
        ``extends`` edge terminates at *file_node* or at a symbol it
        defines.

        Implementor detection drives the confidence cap on
        ``interface``-kind unused-export findings. Resolution quality
        varies by language (C# DI containers, Java reflection, Swift
        protocol extensions etc.), so an interface with zero observed
        implementors should be treated as "missing signal", not
        "confirmed dead".
        """
        implementor_edges = ("implements", "method_implements", "extends")
        # File-level incoming edges (XAML bindings, framework edges)
        for pred in self.graph.predecessors(file_node):
            if self.graph[pred][file_node].get("edge_type") in implementor_edges:
                return True
        # Symbol-level incoming edges — interfaces typically receive
        # ``implements`` edges on the type symbol, not on the file.
        for succ in self.graph.successors(file_node):
            succ_data = self.graph.nodes.get(succ, {})
            if succ_data.get("node_type") != "symbol":
                continue
            for pred in self.graph.predecessors(succ):
                if self.graph[pred][succ].get("edge_type") in implementor_edges:
                    return True
        return False

    def _matches_dynamic_patterns(self, path: str, patterns: tuple[str, ...]) -> bool:
        name = Path(path).stem
        return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)

    def _name_matches_dynamic(self, name: str, patterns: tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)

    def _is_old(self, dt: Any, days: int = 180) -> bool:
        """Return True if datetime is older than `days` ago."""
        if dt is None:
            return False
        now = datetime.now(UTC)
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return (now - dt).days > days
        return False

