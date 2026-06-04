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
import os
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from .constants import (
    _DEFAULT_DYNAMIC_PATTERNS,
    _FRAMEWORK_DECORATOR_SUFFIXES,
    _FRAMEWORK_DECORATORS,
    _NEVER_FLAG_PATTERNS,
    _NEVER_PACKAGE_DIRS,
    _NON_CODE_LANGUAGES,
    _is_fixture_path,
)
from .contract_methods import is_contract_method
from .cpp_reachability import (
    build_cpp_package_files,
    is_cpp_file_reachable,
    is_cpp_path,
)
from .go_reachability import build_go_package_files, is_go_file_reachable
from .jvm_reachability import build_jvm_package_files, is_jvm_file_reachable

# Symbol kinds that cannot be independently imported by name in any
# supported language. Flagging them as "unused exports" is a guaranteed
# false-positive — they're always accessed through an enclosing class /
# namespace. C# auto-properties land in the graph as ``variable``;
# fields / enum members / type aliases / namespace anchors share the
# same property.
_UNIVERSAL_NON_IMPORTABLE: frozenset[str] = frozenset({
    "method",
    "variable",
    "field",
    "property",
    "enum_member",
    "constant",
    "type_alias",
    "namespace",
    "module",
})

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


def _non_importable_kinds(language: str) -> frozenset[str]:
    """Per-language set of symbol kinds excluded from unused-export passes.

    Returns the union of the universal set and any language-specific
    additions. Cheap to call — short lookup, no per-call allocation
    when the language has no additions.
    """
    extra = _LANGUAGE_NON_IMPORTABLE.get(language)
    if extra is None:
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
_UNCALLABLE_TYPE_KINDS: frozenset[str] = frozenset({
    "struct",
    "interface",
    "enum",
    "type_alias",
})

# Symbol names that are language-runtime entry points or compiler-implicit
# anchors — never invoked by user-authored callers, never dead.
_ENTRY_POINT_SYMBOL_NAMES: frozenset[str] = frozenset({
    "Main",                # C#, Java, Kotlin, Go, Rust, Swift, Scala
    "main",                # most others
    # ---- Go runtime / test conventions ------------------------------
    # ``func init`` is run by the Go runtime when the package is linked —
    # never called by name, so it has no inbound call edge. ``TestMain``
    # is the test-binary entry the ``go test`` runner invokes by reflection.
    "init",
    "TestMain",
    "MauiProgram",         # .NET MAUI
    "Program",             # C# top-level / classic console
    "Startup",             # ASP.NET Core legacy
    "__module__",          # synthetic per-file module anchor
    "_start",              # C runtime
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
    "DllInstall",          # legacy MSI custom-action entry
    # ---- Win32 GUI / console entry points ---------------------------
    "wWinMain",            # Unicode WinMain
    "WinMain",             # ANSI WinMain
    "wmain",               # Unicode console main
    "ServiceMain",         # Win32 service entry
    # ---- libFuzzer / Honggfuzz / AFL fuzz harness entries ------------
    # The fuzzer driver invokes these by name via dlsym; no static
    # caller will ever exist.
    "LLVMFuzzerTestOneInput",
    "LLVMFuzzerInitialize",
    # ---- Windows hook / ETW callbacks invoked by macros / runtime ----
    "LowLevelKeyboardProc",
    "LowLevelMouseProc",
    "RegisterProvider",    # ETW provider registration (macro-invoked)
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
    "canEqual",                    # Lombok-equivalent generated method
    "INSTANCE",                    # Kotlin object singleton field
    "Companion",                   # Kotlin companion-object accessor
})


# Compiler-intrinsic preprocessor macros C/C++ libraries redefine as a
# fallback for compilers that don't ship them (``#if !defined(__has_include)
# \n#define __has_include(h) 0``). The tree-sitter cpp grammar extracts the
# ``#define`` as a ``preproc_function_def`` symbol, but the call sites are
# preprocessor ``#if __has_include(...)`` directives, which the static
# graph cannot observe — so without this skip every such fallback flags
# as an unused export.
_CPP_BUILTIN_MACROS: frozenset[str] = frozenset({
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
    "__FILE__", "__LINE__", "__DATE__", "__TIME__",
    "__func__", "__FUNCTION__", "__PRETTY_FUNCTION__",
})
from .dynamic_markers import find_dynamic_edge_files, find_dynamic_import_files
from .models import DeadCodeFindingData, DeadCodeKind, DeadCodeReport

logger = structlog.get_logger(__name__)

# Re-export barrel filenames. Skipped in the *unreachable-file* pass only:
# a barrel aggregates other modules' symbols and is reached by importing
# those names (or, for a package's public entry, via package.json
# ``exports``/``main``), so a barrel with no inbound graph edge is not dead.
# They are NOT skipped in the unused-export pass — a genuine symbol defined
# in a barrel that nobody imports should still be flagged.
_BARREL_FILENAMES: frozenset[str] = frozenset({
    "__init__.py",
    "index.ts",
    "index.tsx",
    "index.js",
    "index.jsx",
    "index.mts",
    "index.cts",
    "index.mjs",
    "index.cjs",
})


def _find_jsx_namespace_files(parsed_files: dict) -> set[str]:
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
            source = src_path.read_text(errors="ignore")
            # Match ``namespace JSX`` and ``declare namespace JSX`` — both
            # are JSX transformer integration points in practice.
            if "namespace JSX" in source:
                matches.add(path)
        except Exception:
            continue
    return matches


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


@lru_cache(maxsize=8)
def _never_flag_regex(patterns: tuple[str, ...]) -> re.Pattern[str]:
    """Compile *patterns* into one alternation regex equivalent to fnmatch.

    ``fnmatch.fnmatch(path, p)`` normcases both sides and matches the
    translated glob; doing that per (node x pattern) costs ~540 fnmatch
    calls per node and dominated the whole dead-code pass (measured: 50s of
    a 51s analyze() on a 13k-node graph, mostly Windows ``normcase``).
    One pre-normcased alternation keeps the exact same match semantics at
    one regex match per node.
    """
    return re.compile("|".join(fnmatch.translate(os.path.normcase(p)) for p in patterns))


class DeadCodeAnalyzer:
    """Detects unreachable files, unused exports, unused internals, and
    zombie packages using the dependency graph and git metadata.
    """

    def __init__(
        self,
        graph: Any,  # nx.DiGraph
        git_meta_map: dict | None = None,
        parsed_files: dict | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}
        self._dynamic_import_files = find_dynamic_import_files(
            parsed_files or {}
        ) | find_dynamic_edge_files(graph)
        self._jsx_namespace_files: set[str] = _find_jsx_namespace_files(
            parsed_files or {}
        )
        # Lazily-built ``.go`` package-directory → file-node map, used by the
        # Go package-granular reachability hook (see ``go_reachability``).
        self._go_package_files: dict[str, list[str]] | None = None
        # Lazily-built JVM (``.java`` + ``.kt``) package-directory map; see
        # :mod:`jvm_reachability`.
        self._jvm_package_files: dict[str, list[str]] | None = None
        # Lazily-built C/C++ directory map; see :mod:`cpp_reachability`.
        self._cpp_package_files: dict[str, list[str]] | None = None

    def _go_packages(self) -> dict[str, list[str]]:
        """Return the cached Go package map, building it on first use."""
        if self._go_package_files is None:
            self._go_package_files = build_go_package_files(self.graph)
        return self._go_package_files

    def _jvm_packages(self) -> dict[str, list[str]]:
        """Return the cached JVM package map, building it on first use."""
        if self._jvm_package_files is None:
            self._jvm_package_files = build_jvm_package_files(self.graph)
        return self._jvm_package_files

    def _cpp_packages(self) -> dict[str, list[str]]:
        """Return the cached C/C++ directory map, building it on first use."""
        if self._cpp_package_files is None:
            self._cpp_package_files = build_cpp_package_files(self.graph)
        return self._cpp_package_files

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

        min_conf = cfg.get("min_confidence", 0.4)
        findings = [f for f in findings if f.confidence >= min_conf]

        now = datetime.now(UTC)
        deletable = sum(f.lines for f in findings if f.safe_to_delete)

        high = sum(1 for f in findings if f.confidence >= 0.7)
        medium = sum(1 for f in findings if 0.4 <= f.confidence < 0.7)
        low = sum(1 for f in findings if f.confidence < 0.4)

        return DeadCodeReport(
            repo_id="",
            analyzed_at=now,
            total_findings=len(findings),
            findings=findings,
            deletable_lines=deletable,
            confidence_summary={"high": high, "medium": medium, "low": low},
        )

    def analyze_partial(
        self, affected_files: list[str], config: dict | None = None
    ) -> DeadCodeReport:
        """Run the full detector suite, then narrow findings to ``affected_files``.

        Persisted via the file-scoped ``upsert_dead_code_findings`` so unchanged
        files keep their findings. Cross-file effects on unchanged files are not
        recomputed here; the full ``analyze()`` remains authoritative.
        """
        affected_set = set(affected_files)
        full = self.analyze(config)
        findings = [f for f in full.findings if f.file_path in affected_set]

        deletable = sum(f.lines for f in findings if f.safe_to_delete)
        high = sum(1 for f in findings if f.confidence >= 0.7)
        medium = sum(1 for f in findings if 0.4 <= f.confidence < 0.7)
        low = sum(1 for f in findings if f.confidence < 0.4)

        return DeadCodeReport(
            repo_id="",
            analyzed_at=full.analyzed_at,
            total_findings=len(findings),
            findings=findings,
            deletable_lines=deletable,
            confidence_summary={"high": high, "medium": medium, "low": low},
        )

    # ------------------------------------------------------------------
    # Detection methods
    # ------------------------------------------------------------------

    def _detect_unreachable_files(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect files with in_degree == 0 that are not entry points, tests, or config."""
        findings = []

        for node in self.graph.nodes():
            if _is_synthetic_node(str(node)):
                continue

            node_data = self.graph.nodes[node]
            if node_data.get("language", "unknown") in _NON_CODE_LANGUAGES:
                continue
            if node_data.get("is_entry_point", False):
                continue
            if node_data.get("is_test", False):
                continue
            if _is_fixture_path(str(node)):
                continue
            if self._should_never_flag(str(node), whitelist):
                continue
            # Re-export barrels (index.* / __init__.py) are reached by the
            # names they forward or via package ``exports``/``main`` — a barrel
            # with no inbound graph edge is not dead code.
            if Path(str(node)).name in _BARREL_FILENAMES:
                continue
            if self._is_api_contract(node_data):
                continue

            # Go reachability is package-granular: a file with no direct
            # importer can still be live (entry-package sibling next to
            # main.go, or a package whose siblings carry the import). Delegate
            # to the Go helper instead of the raw file-level in_degree check.
            node_str = str(node)
            if node_str.endswith(".go"):
                if is_go_file_reachable(node_str, self.graph, self._go_packages()):
                    continue
            elif node_str.endswith(".java") or node_str.endswith(".kt"):
                # JVM reachability is package-aware too: sibling-rescued
                # packages plus stereotype-annotated / ``main``-carrying
                # files surface as live even with no direct importer.
                if is_jvm_file_reachable(node_str, self.graph, self._jvm_packages()):
                    continue
            elif is_cpp_path(node_str):
                # C/C++ reachability rescues public-API headers, ``main``-
                # bearing TUs (apps/demos/benchmarks/fuzzers), internal
                # headers next to their implementation files, and
                # conditional-compile alternates that share a stem prefix.
                if is_cpp_file_reachable(node_str, self.graph, self._cpp_packages()):
                    continue
            elif self.graph.in_degree(node) > 0:
                continue

            finding = self._make_unreachable_finding(str(node), node_data, dynamic_patterns)
            if finding:
                findings.append(finding)

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

        # Reduce confidence when dynamic imports exist in the same package.
        if self._dynamic_import_files:
            node_pkg = str(Path(node).parent)
            for dif in self._dynamic_import_files:
                if str(Path(dif).parent) == node_pkg:
                    confidence = min(confidence, 0.4)
                    break

        safe = confidence >= 0.7
        if safe and self._matches_dynamic_patterns(node, dynamic_patterns):
            safe = False

        evidence = ["in_degree=0 (no files import this)"]
        if commit_90d == 0:
            evidence.append("No commits in last 90 days")
        if self._dynamic_import_files and confidence <= 0.4:
            evidence.append("Package uses dynamic imports or runtime-resolved edges")

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
            package=self._get_package(node),
            evidence=evidence,
            safe_to_delete=safe,
            primary_owner=primary_owner,
            age_days=age_days,
        )

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
            # classpath scanning. Mirror the entry-point skip the
            # ``_detect_unreachable_files`` pass already does so an
            # ``@RestController`` class isn't reported as unused.
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
            file_dynamically_loaded = any(
                self.graph.get_edge_data(pred, node, {}).get("edge_type")
                in ("dynamic_uses", "dynamic", "framework")
                for pred in self.graph.predecessors(node)
            )
            if file_dynamically_loaded:
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

                # Decorators are stored with the leading "@" (e.g. "@app.route").
                # _FRAMEWORK_DECORATORS entries are bare prefixes; suffixes
                # like ``.command`` match locally-named Click groups
                # (``@my_group.command("add")``). Compare against the
                # stripped form, and strip any call ``(...)`` tail so the
                # suffix check sees the attribute path itself.
                decorators = sym.get("decorators", [])

                def _decorator_base(d: str) -> str:
                    stripped = d.lstrip("@")
                    paren = stripped.find("(")
                    return stripped[:paren] if paren >= 0 else stripped

                if any(
                    _decorator_base(d).startswith(prefix)
                    for d in decorators
                    for prefix in _FRAMEWORK_DECORATORS
                ):
                    continue
                if any(
                    _decorator_base(d).endswith(suffix)
                    for d in decorators
                    for suffix in _FRAMEWORK_DECORATOR_SUFFIXES
                ):
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

                is_deprecated = any(
                    sym_name.endswith(suffix) for suffix in ("_DEPRECATED", "_LEGACY", "_COMPAT")
                )

                has_importers = False
                for pred in self.graph.predecessors(node):
                    edge_data = self.graph[pred][node]
                    imported_names = edge_data.get("imported_names", [])
                    if sym_name in imported_names or "*" in imported_names:
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
                    self.graph[pred][sym_id].get("edge_type")
                    in ("calls", "method_implements", "reads",
                        "extends", "implements", "type_use")
                    for pred in self.graph.predecessors(sym_id)
                ):
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
                    confidence = min(confidence, 0.4)

                # COM / IUnknown / IDispatch contract methods
                # (``QueryInterface``, ``AddRef``, ``Release``, …) are
                # dispatched through native vtables — no static caller
                # edge will ever land in the graph. Clamp below the
                # safe-to-delete threshold so we never ship them as
                # confident dead code on Windows / COM-heavy C++ repos.
                if is_contract_method(
                    sym_name, sym.get("kind"), sym.get("language", "unknown")
                ):
                    confidence = min(confidence, 0.4)

                safe = confidence >= 0.7

                git_meta = self.git_meta_map.get(str(node), {})

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
                        package=self._get_package(str(node)),
                        evidence=[f"No imports of '{sym_name}' found in graph"],
                        safe_to_delete=safe,
                        primary_owner=git_meta.get("primary_owner_name"),
                        age_days=git_meta.get("age_days"),
                    )
                )

        return findings

    def _detect_unused_internals(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect private/internal symbols with zero incoming call edges.

        Off by default (higher false-positive rate). Enable with
        ``detect_unused_internals=True`` in the config dict.
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
            if node_data.get("visibility") not in ("private", "internal"):
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
            if decorators:
                def _dec_base(d: str) -> str:
                    stripped = d.lstrip("@")
                    paren = stripped.find("(")
                    return stripped[:paren] if paren >= 0 else stripped

                if any(
                    _dec_base(d).startswith(prefix)
                    for d in decorators
                    for prefix in _FRAMEWORK_DECORATORS
                ):
                    continue
                if any(
                    _dec_base(d).endswith(suffix)
                    for d in decorators
                    for suffix in _FRAMEWORK_DECORATOR_SUFFIXES
                ):
                    continue

            has_callers = any(
                self.graph.get_edge_data(pred, node, {}).get("edge_type") == "calls"
                for pred in self.graph.predecessors(node)
            )
            if has_callers:
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
            findings.append(
                DeadCodeFindingData(
                    kind=DeadCodeKind.UNUSED_INTERNAL,
                    file_path=file_path,
                    symbol_name=sym_name,
                    symbol_kind=node_data.get("kind"),
                    confidence=0.65,
                    reason=f"Private symbol '{sym_name}' has no callers",
                    last_commit_at=git_meta.get("last_commit_at")
                    if isinstance(git_meta.get("last_commit_at"), datetime)
                    else None,
                    commit_count_90d=git_meta.get("commit_count_90d", 0),
                    lines=node_data.get("end_line", 0) - node_data.get("start_line", 0),
                    package=self._get_package(file_path),
                    evidence=[f"No CALL edges to '{sym_name}'"],
                    safe_to_delete=False,
                    primary_owner=git_meta.get("primary_owner_name"),
                    age_days=git_meta.get("age_days"),
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
                self.graph.nodes.get(f, {}).get("language", "unknown")
                not in _NON_CODE_LANGUAGES
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
                    gm = self.git_meta_map.get(f)
                    if gm is None:
                        continue
                    f_last = getattr(gm, "last_commit_at", None)
                    if f_last and (pkg_last_commit is None or f_last > pkg_last_commit):
                        pkg_last_commit = f_last
                    pkg_total_commits_90d += getattr(gm, "commit_count_90d", 0) or 0
                    f_owner = getattr(gm, "primary_owner_name", None)
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
                        package=pkg,
                        evidence=[f"No inter-package imports into '{pkg}'"],
                        safe_to_delete=False,
                        primary_owner=pkg_owner,
                        age_days=pkg_age_days,
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_never_flag(self, path: str, whitelist: set[str]) -> bool:
        """Return True if path should never be flagged as dead."""
        if path in whitelist:
            return True
        if _never_flag_regex(_NEVER_FLAG_PATTERNS).match(os.path.normcase(path)):
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

    def _is_api_contract(self, node_data: dict) -> bool:
        return node_data.get("is_api_contract", False)

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

    def _get_package(self, path: str) -> str | None:
        parts = Path(path).parts
        return parts[0] if len(parts) > 1 else None
