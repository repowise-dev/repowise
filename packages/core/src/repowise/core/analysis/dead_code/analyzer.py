"""DeadCodeAnalyzer â€” pure graph + git-metadata dead-code detection.

All analysis is graph traversal + SQL. No LLM calls. Must complete in
< 10 seconds.

The four detection passes (unreachable files, unused exports, unused
internals, zombie packages) live as methods on this class. Constants,
data models, dynamic-import markers and the symbol, decorator and
source-scan rules live in sibling modules under this package.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Iterator
from collections.abc import Set as AbstractSet
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ...code_origin import code_origin
from ...ingestion.models import REACHABILITY_USE_EDGE_TYPES
from ...ingestion.symbol_identity import base_symbol_id, overload_sets
from .apex_reachability import ApexLiveness, settle_apex_findings
from .c_name_uses import (
    DEFINITION_HEADER_LINES,
    DeclarationSites,
    drop_preprocessed_named_elsewhere,
)
from .constants import (
    _DEFAULT_DYNAMIC_PATTERNS,
    _PREPROCESSED_LANGUAGES,
    never_flag_path,
)
from .csharp_reachability import (
    build_csharp_named_files,
    build_csharp_named_types,
    scanned_implementations,
)
from .decorator_rules import (  # noqa: F401  (moved rules re-exported)
    _DEPRECATED_DECORATOR_BASES,
    _QUOTED_STR_RE,
    _carries_argument,
    _declared_deliberately_unused,
    _decorator_bases,
    _is_framework_registered,
    _is_receiver_registration,
    _is_symbol_deprecated,
)
from .deletion_gate import apply_deletion_gate
from .dynamic_markers import (
    find_dynamic_edge_files,
    find_dynamic_import_files,
    read_source_text,
)
from .entry_shape import (
    clamp_entry_shaped,
    drop_program_entries,
)
from .file_reachability import (
    PackageFileMap,
    ReachabilityRescues,
    build_package_file_map,
)
from .finding_helpers import (  # noqa: F401  (moved helpers re-exported)
    _LINES_UNKNOWN,
    _csharp_exports,
    _is_synthetic_node,
    _names_by_file,
    _symbol_span,
)
from .go_name_uses import drop_go_package_uses
from .kotlin_multiplatform import settle_platform_declarations
from .meta_annotations import MetaAnnotations
from .models import DeadCodeFindingData, DeadCodeKind, DeadCodeReport
from .module_strings import drop_named_modules
from .name_occurrences import (
    IDENTIFIER_RE,
    clamp_named_types,
    clamp_path_mentions,
    clamp_unverified_absence,
    demote_exports_used_in_own_code,
    demote_used_in_own_file,
    drop_bare_name_uses,
    drop_internals_used_in_own_file,
    drop_reference_assembly_api,
)
from .passes.unreachable_files import UnreachableFilePass
from .passes.unused_exports import UnusedExportPass
from .passes.unused_internals import UnusedInternalPass
from .passes.zombie_packages import ZombiePackagePass
from .published_api import BuildFacts, demote_published_api
from .risk_factors import (
    RISK_CAP_CONFIDENCE,
)
from .source_prepasses import (  # noqa: F401  (moved scans re-exported)
    _BUNDLER_CONFIG_STEMS,
    _RELATIVE_PATH_STRING_RE,
    _TS_PROBE_SUFFIXES,
    _find_bundler_alias_targets,
    _find_jsx_namespace_files,
    _find_ts_export_aliases,
    _posix_normpath,
)
from .swift_reachability import build_swift_named_files
from .symbol_rules import (  # noqa: F401  (moved rules re-exported)
    _CPP_BUILTIN_MACROS,
    _CPP_TYPE_DECLARATION_KINDS,
    _ENTRY_POINT_SYMBOL_NAMES,
    _LANGUAGE_NON_IMPORTABLE,
    _NON_IMPORTABLE_SYMBOL_KINDS,
    _SFC_LANGUAGES,
    _SFC_NON_IMPORTABLE_KINDS,
    _TS_JS_IMPORTABLE_KINDS,
    _TYPE_DECLARATION_KINDS,
    _UNCALLABLE_TYPE_KINDS,
    _UNIVERSAL_NON_IMPORTABLE,
    _is_compiler_invoked,
    _is_declaration_only,
    _is_public_top_level,
    _nested_class_used,
    _non_importable_kinds,
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
#: are large â€” one corpus repo ships a 104 MB generated parser â€” so the scan is
#: bounded per file and in total. Tree-sitter costs ~95 MB of RSS per MB of
#: source; this pass is a byte scan and costs far less, but the bound is what
#: keeps it from ever becoming the expensive thing.
_UNINDEXED_SCAN_PER_FILE_BYTES = 4 * 1024 * 1024
_UNINDEXED_SCAN_TOTAL_BYTES = 32 * 1024 * 1024


logger = structlog.get_logger(__name__)


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
        dotnet_index: Any | None = None,
    ) -> None:
        self.graph = graph
        # The C# resolver's project index (``GraphBuilder.dotnet_index``):
        # scopes the C# name rescue to the projects that can see a file.
        self._dotnet_index = dotnet_index
        self._csharp_named_files: frozenset[str] | None = None
        self._swift_named_files: frozenset[str] | None = None
        self._apex_liveness: ApexLiveness | None = None
        # C# classes a plugin host or container finds by type, built on first use.
        self._csharp_discovered: frozenset[tuple[str, str]] | None = None
        self._parsed_files = parsed_files or {}
        self.git_meta_map = git_meta_map or {}
        # Source files ingestion could not read (currently: dropped on size).
        # An unread importer is invisible to the graph, so "no importers" stops
        # meaning "unused" and starts meaning "we did not look" â€” which is how
        # one skipped entry point turned into 38 deletion-ready components at
        # high confidence (#1237). ``(path, reason)`` pairs; the reason rides
        # along so the evidence line can say why.
        self._unindexed_source_files = list(unindexed_source_files or [])
        self._repo_root = repo_root
        self._source_map = source_map
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
        # the unreachable-file pass asks "does any dynamic-import file sit
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
        # Files substituted in via bundler ``resolve.alias`` config â€” named
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
        # Cache for JSX prop-guard extraction: maps pred_file â†’ list of
        # (parent_fn, child_component, guard_prop) triples. Populated lazily
        # by _get_unsatisfied_prop_guard so each TSX/JSX file is read from
        # disk and parsed by tree-sitter at most once per analysis run.
        self._guard_cache: dict[str, list[tuple[str, str, str]]] = {}
        # {base id: overload member ids}, built on first use.
        self._overload_units: dict[str, list[str]] | None = None
        # Repo annotation classes that register what they annotate, built on first use.
        self._meta_annotations: MetaAnnotations | None = None

    def _framework_meta_annotations(self, path: str) -> frozenset[str]:
        """Names of repo annotation classes that register what they annotate in *path*."""
        if self._meta_annotations is None:
            self._meta_annotations = MetaAnnotations(self._source_map, _is_framework_registered)
        return self._meta_annotations.visible_in(path)

    def _csharp_discovered_classes(self) -> frozenset[tuple[str, str]]:
        """``(file, class)`` the runtime instantiates by type; see :func:`scanned_implementations`."""
        if self._csharp_discovered is None:
            self._csharp_discovered = scanned_implementations(
                self.graph, self._source_map, self._parsed_files
            )
        return self._csharp_discovered

    def _reachability_rescues(self, whitelist: AbstractSet[str]) -> ReachabilityRescues:
        """Assemble the rescue state the shared predicate reads."""
        if self._package_files is None:
            self._package_files = build_package_file_map(self.graph)
        if self._csharp_named_files is None:
            self._csharp_named_files = build_csharp_named_files(
                self.graph,
                self._source_map,
                dotnet_index=self._dotnet_index,
                repo_root=self._repo_root,
                discovered=self._csharp_discovered_classes(),
            )
        if self._swift_named_files is None:
            self._swift_named_files = build_swift_named_files(self.graph, self._source_map)
        return ReachabilityRescues(
            bundler_alias_targets=frozenset(self._bundler_alias_targets),
            whitelist=frozenset(whitelist),
            package_files=self._package_files,
            csharp_named_files=self._csharp_named_files,
            swift_named_files=self._swift_named_files,
            apex_live_files=self._apex().live,
        )

    def _apex(self) -> ApexLiveness:
        """Apex liveness, built on first use (see :mod:`apex_reachability`)."""
        if self._apex_liveness is None:
            self._apex_liveness = ApexLiveness(
                self.graph,
                self._source_map,
                self._parsed_files,
                self._repo_root,
                frozenset(t.casefold().encode() for t in self._unindexed_identifier_tokens()),
            )
        return self._apex_liveness

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
        dynamic_patterns = cfg.get("dynamic_patterns", _DEFAULT_DYNAMIC_PATTERNS)
        # Machine output known only by its content (a webpack chunk, a minified
        # bundle) joins the whitelist, which every pass already honours.
        whitelist = set(cfg.get("whitelist", [])) | self._content_machine_output()

        findings = self._run_passes(cfg, dynamic_patterns, whitelist, on_step)
        findings = self._settle(findings)

        min_conf = cfg.get("min_confidence", RISK_CAP_CONFIDENCE)
        hidden_below_threshold = sum(1 for f in findings if f.confidence < min_conf)
        findings = [f for f in findings if f.confidence >= min_conf]

        return DeadCodeReport.from_findings(
            findings, hidden_below_threshold=hidden_below_threshold
        )

    def _run_passes(
        self,
        cfg: dict,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
        on_step: Any | None,
    ) -> list[DeadCodeFindingData]:
        """Run each enabled detection pass in order, reporting each stage to *on_step*."""
        passes: tuple[tuple[str, str, Callable[[], list[DeadCodeFindingData]]], ...] = (
            ("detect_unreachable_files", "unreachable_files",
             lambda: self._detect_unreachable_files(whitelist)),
            ("detect_unused_exports", "unused_exports",
             lambda: self._detect_unused_exports(dynamic_patterns, whitelist)),
            ("detect_unused_internals", "unused_internals",
             lambda: self._detect_unused_internals(dynamic_patterns, whitelist)),
            ("detect_zombie_packages", "zombie_packages",
             lambda: self._detect_zombie_packages(whitelist)),
        )
        findings: list[DeadCodeFindingData] = []
        for flag, stage, run in passes:
            if cfg.get(flag, True):
                findings.extend(run())
                if on_step:
                    on_step(stage)
        return findings

    def _settle(self, findings: list[DeadCodeFindingData]) -> list[DeadCodeFindingData]:
        """Drop, demote and clamp the pass findings on evidence the graph does not carry."""
        # First: an Apex name written in any case, a C# type another file in
        # its scope names, and a C/C++ name written outside its declaration are
        # uses. Each settles the finding before any clamp re-scores it.
        findings = settle_apex_findings(findings, self._apex(), self._source_map)
        findings = self._drop_csharp_named_exports(findings)
        findings = drop_preprocessed_named_elsewhere(
            findings,
            self._source_map,
            self._preprocessed_declaration_sites(),
            self._unindexed_identifier_tokens(),
        )
        # Same for Go, scoped to the package and to importers naming it.
        findings = drop_go_package_uses(findings, self._source_map, self.graph)
        # Before the confidence filter, not after: a finding an unread importer
        # could explain must be able to fall *below* min_confidence and drop
        # out entirely, rather than being reported at a number it no longer
        # deserves.
        findings = self._clamp_for_unindexed_importers(findings)
        # Before the name search: an ``expect``'s own ``actual`` files write its
        # name, which that search would read as a use elsewhere.
        findings = settle_platform_declarations(findings, self._source_map)
        # Same position and reason, asking the wider question: did we look
        # anywhere except the import graph.
        findings = drop_bare_name_uses(findings, self._source_map)
        # Before the clamp, which would otherwise read a same-file use as
        # "named elsewhere" and leave the row in the default view.
        findings = demote_exports_used_in_own_code(findings, self._source_map)
        findings = clamp_unverified_absence(findings, self._source_map)
        findings = drop_internals_used_in_own_file(findings, self._source_map)
        findings = clamp_path_mentions(findings, self._source_map)
        findings = drop_program_entries(findings, self._source_map)
        type_names = self._public_top_level_names(findings, kinds=_TYPE_DECLARATION_KINDS)
        findings = drop_reference_assembly_api(findings, self._source_map, type_names)
        findings = clamp_named_types(findings, self._source_map, type_names)
        findings = drop_named_modules(findings, self._source_map, self._dynamic_import_files)
        findings = clamp_entry_shaped(
            findings, self._source_map, self._public_top_level_names(findings)
        )

        findings = demote_published_api(
            findings,
            self._published_api_languages(findings, type_names),
            BuildFacts(repo_root=self._repo_root, dotnet_index=self._dotnet_index),
        )

        return apply_deletion_gate(findings, self.graph, self._repo_root)

    def _detect_zombie_packages(self, whitelist: set[str]) -> list[DeadCodeFindingData]:
        """Detect monorepo packages with no incoming inter_package edges."""
        return ZombiePackagePass(self).run(whitelist)

    def _drop_csharp_named_exports(
        self, findings: list[DeadCodeFindingData]
    ) -> list[DeadCodeFindingData]:
        """Drop C# unused exports whose type a file that can see it names.

        The symbol-level counterpart of the file rescue in
        :func:`build_csharp_named_files`: a C# type is used from its own
        namespace with no import, so "no importer" says nothing about it. A
        project-scoped name is the same evidence the file pass drops on. One
        its own file uses is capped below the floor instead (see
        :func:`demote_used_in_own_file`). Returns a new list.
        """
        exports = _csharp_exports(findings)
        if not exports:
            return findings
        named = build_csharp_named_types(
            self.graph,
            self._source_map,
            _names_by_file(exports),
            dotnet_index=self._dotnet_index,
            repo_root=self._repo_root,
            discovered=self._csharp_discovered_classes(),
        )
        dropped = {id(f) for f in exports if (f.file_path, f.symbol_name) in named}
        demote_used_in_own_file([f for f in exports if id(f) not in dropped], self._source_map)
        return [f for f in findings if id(f) not in dropped]

    def _published_api_languages(
        self, findings: list[DeadCodeFindingData], type_names: dict[str, frozenset[str]]
    ) -> dict[str, str]:
        """Language of each file whose finding is about public API.

        Every unused export is; an unreachable file only when it declares a
        public type (*type_names*).
        """
        files = {
            f.file_path
            for f in findings
            if f.kind is DeadCodeKind.UNUSED_EXPORT or type_names.get(f.file_path)
        }
        return {
            path: self.graph.nodes[path].get("language", "")
            for path in files
            if self.graph.has_node(path)
        }

    # ------------------------------------------------------------------
    # Detection methods
    # ------------------------------------------------------------------

    def _detect_unreachable_files(
        self,
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect files nothing can reach that are not tests, fixtures, or config."""
        return UnreachableFilePass(self).run(whitelist)

    def _detect_unused_internals(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect private symbols with zero incoming call edges."""
        return UnusedInternalPass(self).run(dynamic_patterns, whitelist)

    def _detect_unused_exports(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect public symbols with no incoming edges."""
        return UnusedExportPass(self).run(dynamic_patterns, whitelist)

    def _preprocessed_declaration_sites(self) -> DeclarationSites:
        """Where each C/C++ symbol name is declared: a prototype's whole span, a
        definition's header lines. An occurrence there is not a use of it."""
        sites: dict[str, list[tuple[str, int, int]]] = {}
        for _, data in self.graph.nodes(data=True):
            if data.get("node_type") != "symbol" or data.get("language") not in _PREPROCESSED_LANGUAGES:
                continue
            start, end = data.get("start_line") or 0, data.get("end_line") or 0
            if not data.get("is_declaration"):
                end = min(end, start + DEFINITION_HEADER_LINES - 1)
            sites.setdefault(data.get("name", ""), []).append((data.get("file_path", ""), start, end))
        return sites

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

        # Sorted so the same skipped set always yields the same evidence text.
        skipped = sorted(path for path, _ in self._unindexed_source_files)
        skipped_names = ", ".join(skipped[:3])
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
            # not hypothetical â€” a published API dump lists a public `add`, and
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

    def _defined_symbols(self, node: Any) -> list[tuple[str, dict]]:
        """``(symbol_id, data)`` for each symbol *node* defines."""
        return [
            (succ, self.graph.nodes[succ])
            for succ in self.graph.successors(node)
            if self.graph.nodes[succ].get("node_type") == "symbol"
            and self.graph.get_edge_data(node, succ, {}).get("edge_type") == "defines"
        ]

    def _public_top_level_names(
        self, findings: list[DeadCodeFindingData], kinds: AbstractSet[str] | None = None
    ) -> dict[str, frozenset[str]]:
        """Top-level public symbol names (of *kinds*, if given) of each unreachable file."""
        return {
            f.file_path: frozenset(
                sym["name"]
                for _, sym in self._defined_symbols(f.file_path)
                if _is_public_top_level(sym, kinds)
            )
            for f in findings
            if f.kind is DeadCodeKind.UNREACHABLE_FILE and self.graph.has_node(f.file_path)
        }

    def _imported_names_by_edge(
        self, node: Any, edge_types: tuple[str, ...] | None = ("imports",)
    ) -> Iterator[list[str]]:
        """``imported_names`` of every edge of *edge_types* into *node* (any type if None)."""
        for pred in self.graph.predecessors(node):
            edge = self.graph.get_edge_data(pred, node, {})
            if edge_types is not None and edge.get("edge_type") not in edge_types:
                continue
            yield edge.get("imported_names", [])

    def _git_fields(self, file_path: str) -> dict[str, Any]:
        """The git-activity fields a symbol finding carries for *file_path*."""
        git_meta = self.git_meta_map.get(file_path, {})
        return {
            "last_commit_at": git_meta.get("last_commit_at")
            if isinstance(git_meta.get("last_commit_at"), datetime)
            else None,
            "commit_count_90d": git_meta.get("commit_count_90d", 0),
            "primary_owner": git_meta.get("primary_owner_name"),
            "age_days": git_meta.get("age_days"),
        }

    def _get_unsatisfied_prop_guard(
        self,
        sym_id: str,
        sym_name: str,
        visited: set[str] | None = None,
        memo: dict[str, tuple[str, str] | None] | None = None,
    ) -> tuple[str, str] | None:
        """Check if a symbol is rendered behind a prop guard that is never supplied."""
        from .jsx_prop_guards import unsatisfied_prop_guard

        return unsatisfied_prop_guard(
            self.graph, self._guarded_renders, sym_id, sym_name, visited, memo
        )

    def _guarded_renders(self, pred_file: str) -> list[tuple[str, str, str]]:
        """``extract_guarded_jsx_renders`` for *pred_file*, read and parsed at most once."""
        if pred_file not in self._guard_cache:
            from .jsx_prop_guards import extract_guarded_jsx_renders

            src = self._read_file_text(pred_file)
            self._guard_cache[pred_file] = (
                extract_guarded_jsx_renders(pred_file, src) if src else []
            )
        return self._guard_cache[pred_file]

    def _file_line_count(self, file_path: str) -> int | None:
        """Physical line count of an indexed file, or ``None`` when unread.

        Prefers ingestion's bytes; falls back to disk only under a known repo
        root, so a missing source is reported as unknown rather than guessed.
        """
        data = self._source_map.get(file_path)
        if data is None and self._repo_root is not None:
            try:
                data = (Path(self._repo_root) / file_path).read_bytes()
            except OSError:
                return None
        if data is None:
            return None
        return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)

    def _read_file_text(self, file_path: str) -> str | None:
        """Safely read the content of a file, using source_map if available."""
        try:
            if getattr(self, "_source_map", None) is not None:
                abs_path = (
                    str(Path(self._repo_root) / file_path)
                    if getattr(self, "_repo_root", None) and not Path(file_path).is_absolute()
                    else file_path
                )
                return read_source_text(file_path, abs_path, self._source_map)
            p = Path(file_path)
            if not p.is_absolute() and getattr(self, "_repo_root", None):
                p = Path(self._repo_root) / p
            if p.exists():
                return p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("read_file_text_failed", path=file_path, error=str(exc))
        return None

    def _overload_unit(self, node: str) -> list[str]:
        """The members of the overload set *node* belongs to or names, else ``[]``.

        A plain id names its set when the set's members carry discriminators,
        which is how a composed constructor id ``path::X::X`` still finds
        ``X::X#0`` and ``X::X#2``.
        """
        if self._overload_units is None:
            self._overload_units = overload_sets(
                n for n in self.graph.nodes if isinstance(n, str)
            )
        return self._overload_units.get(base_symbol_id(node), [])

    def _has_inbound_use(self, node: str, _visiting: frozenset[str] = frozenset()) -> bool:
        """Whether any reachability-use edge lands on *node* or its overload set."""
        members = self._overload_unit(node)
        if members:
            return any(self._lands_on(member, _visiting) for member in members)
        return self._lands_on(node, _visiting)

    def _lands_on(self, node: str, _visiting: frozenset[str] = frozenset()) -> bool:
        """Whether any reachability-use edge lands on *node* (False if absent).

        An edge from inside *node*'s own span only counts when that caller is
        itself live. *_visiting* holds the nodes being resolved up the call
        chain, so a cycle of contained callers cannot recurse forever.
        """
        if not self.graph.has_node(node):
            return False
        visiting = _visiting | {node}
        for pred in self.graph.predecessors(node):
            if (
                self.graph.get_edge_data(pred, node, {}).get("edge_type")
                not in REACHABILITY_USE_EDGE_TYPES
            ):
                continue
            if not self._is_self_use(pred, node):
                return True
            if pred != node and pred not in visiting and self._has_inbound_use(pred, visiting):
                return True
        return False

    def _is_self_use(self, pred: str, node: str) -> bool:
        """Whether *pred* sits inside *node*'s own span, so its edge is no outside use.

        A self-loop, or a caller declared within the symbol's own body, does
        not keep the symbol alive on its own; such a caller decides that only
        if it is itself used from elsewhere (see ``_lands_on``).

        A predecessor whose span is missing or invalid, or that lives in
        another file, is not treated as inside, so
        the edge keeps counting as a use.
        """
        if pred == node:
            return True
        pred_data, node_data = self.graph.nodes[pred], self.graph.nodes[node]
        if pred_data.get("file_path") != node_data.get("file_path"):
            return False
        pred_span, node_span = _symbol_span(pred_data), _symbol_span(node_data)
        if pred_span["start_line"] is None or node_span["start_line"] is None:
            return False
        return (
            node_span["start_line"] <= pred_span["start_line"]
            and pred_span["end_line"] <= node_span["end_line"]
        )


    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _content_machine_output(self) -> set[str]:
        """Files ``code_origin`` reads as generated or vendored from their bytes.

        The path-only rules are asked per candidate by ``never_flag_path``.
        ``is_test=False`` skips the test-path check, which cannot change
        either answer and is half the cost.
        """
        return {
            path
            for path, data in self._source_map.items()
            if code_origin(path, data, is_test=False) in ("generated", "vendored")
        }

    def _should_never_flag(self, path: str, whitelist: set[str]) -> bool:
        """Return True if path should never be flagged as dead.

        Used by the unused-export, unused-internal and zombie-package passes.
        The unreachable-files pass does not call it: every limb below is also
        asked by :func:`is_file_reachable`, which that pass calls anyway, and
        two spellings of one rule is what this phase exists to remove.
        """
        if path in whitelist:
            return True
        if never_flag_path(path):
            return True
        # Workspace-driven never-flag â€” set by language warmups that read
        # the build manifest (Gradle non-``main`` source sets, Cargo
        # ``[[example]]`` / ``[[bench]]`` targets, â€¦). Lets each language
        # learn conventions from its own build files instead of us
        # extending the hardcoded glob list every time a repo defines a
        # custom source set like ``testFixtures`` / ``javaPoet`` /
        # ``jcstress``.
        node = self.graph.nodes.get(path)
        if node is not None and node.get("is_never_flag", False):
            return True
        # __init__.py is a re-export barrel
        return Path(path).name == "__init__.py"

    def _name_matches_dynamic(self, name: str, patterns: tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
