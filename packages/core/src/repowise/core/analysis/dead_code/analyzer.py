"""DeadCodeAnalyzer â€” pure graph + git-metadata dead-code detection.

All analysis is graph traversal + SQL. No LLM calls. Must complete in
< 10 seconds.

The four detection passes (unreachable files, unused exports, unused
internals, zombie packages) live as methods on this class. Constants,
data models, dynamic-import markers and the symbol, decorator and
source-scan rules live in sibling modules under this package.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
from collections.abc import Iterator
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from ...code_origin import code_origin, is_build_file
from ...entry_candidacy import is_reachability_root
from ...ingestion.models import REACHABILITY_USE_EDGE_TYPES
from ...ingestion.package_roots import package_manifest_names
from ...ingestion.symbol_identity import base_symbol_id, overload_sets
from .c_name_uses import (
    DEFINITION_HEADER_LINES,
    DeclarationSites,
    drop_preprocessed_named_elsewhere,
)
from .constants import (
    _DEAD_CODE_EXEMPT_LANGUAGES,
    _DEFAULT_DYNAMIC_PATTERNS,
    _NAMESPACE_IMPORT_LANGUAGES,
    _NEVER_PACKAGE_DIRS,
    _PREPROCESSED_LANGUAGES,
    _PROJECT_FILE_SUFFIXES,
    _RUN_NOT_IMPORTED_LANGUAGES,
    _is_fixture_path,
    never_flag_path,
)
from .contract_methods import is_contract_method
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
    defines_main_function,
    drop_program_entries,
    is_program,
)
from .file_reachability import (
    _NON_DEPENDENCY_EDGES,
    PackageFileMap,
    ReachabilityRescues,
    build_package_file_map,
    is_file_reachable,
)
from .finding_helpers import (
    _LINES_UNKNOWN,
    _csharp_exports,
    _is_synthetic_node,
    _names_by_file,
    _symbol_span,
)
from .go_name_uses import drop_go_package_uses
from .jvm_reachability import defines_main
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
from .passes.unused_exports import UnusedExportPass
from .published_api import BuildFacts, demote_published_api
from .risk_factors import (
    NO_GIT_SIGNAL_CONFIDENCE,
    RISK_CAP_CONFIDENCE,
    path_risk_factors,
    risk_evidence,
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



#: ``(days, confidence)`` rungs for a file with no commit in 90 days, oldest
#: first. ``_is_old`` uses strict >, so days-1 gives >= semantics: a year or
#: more untouched is 1.0, very likely dead.
_GIT_AGE_RUNGS: tuple[tuple[int, float], ...] = ((364, 1.0), (179, 0.9), (89, 0.8))


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
        # Machine output known only by its content (a webpack chunk, a minified
        # bundle) joins the whitelist, which every pass already honours.
        whitelist = set(cfg.get("whitelist", [])) | self._content_machine_output()

        if cfg.get("detect_unreachable_files", True):
            findings.extend(self._detect_unreachable_files(whitelist))
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

        # First: a C# type another file in its scope names is used, and a
        # C/C++ name written outside its declaration is a use. Both settle the
        # finding before any clamp re-scores it.
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
        # Same position and for the same reason. This one asks the wider
        # version of the same question â€” not "could an unread file explain
        # this" but "did we look anywhere except the import graph".
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

        findings = apply_deletion_gate(findings, self.graph, self._repo_root)

        min_conf = cfg.get("min_confidence", RISK_CAP_CONFIDENCE)
        hidden_below_threshold = sum(1 for f in findings if f.confidence < min_conf)
        findings = [f for f in findings if f.confidence >= min_conf]

        return DeadCodeReport.from_findings(
            findings, hidden_below_threshold=hidden_below_threshold
        )

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

    def _detect_unused_exports(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect public symbols with no incoming edges."""
        return UnusedExportPass(self).run(dynamic_patterns, whitelist)

    def _detect_unreachable_files(
        self,
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
            # â€” entry points, API contracts, never-flag globs, the whitelist,
            # barrels, bundler-alias shims and the package-granular languages
            # (Go / JVM / C-C++) â€” lives in the predicate, which the overview
            # assembler calls with the same state so the two cannot disagree.
            # See :mod:`file_reachability`.
            if node_data.get("language", "unknown") in _DEAD_CODE_EXEMPT_LANGUAGES:
                continue
            if node_data.get("is_test", False):
                continue
            if _is_fixture_path(str(node)):
                continue

            if is_file_reachable(str(node), self.graph, rescues):
                continue

            finding = self._make_unreachable_finding(str(node), node_data)
            if finding:
                findings.append(finding)

        return findings

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

    def _make_unreachable_finding(
        self,
        node: str,
        node_data: dict,
    ) -> DeadCodeFindingData | None:
        """Create an unreachable file finding with confidence scoring."""
        git_meta = self.git_meta_map.get(node)
        # No row at all is not "no commits in 90 days": that rung is for a
        # file git has data on. Scored below the deletion-ready threshold and
        # said so in the evidence, on every path that builds this map.
        no_git_signal = not git_meta
        git_meta = git_meta or {}
        commit_90d = git_meta.get("commit_count_90d", 0)
        last_commit = git_meta.get("last_commit_at")
        age_days = git_meta.get("age_days")
        primary_owner = git_meta.get("primary_owner_name")

        confidence = (
            NO_GIT_SIGNAL_CONFIDENCE
            if no_git_signal
            else self._git_age_confidence(commit_90d, last_commit, age_days)
        )

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
        # deletion-ready threshold and surface the factors as evidence â€” the
        # finding still shows up as a review candidate.
        risk_factors = path_risk_factors(node)
        if risk_factors:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        # A namespace-imported file is reached without an edge (a same-namespace
        # ``new T()``), so no edge says little and its age says nothing more.
        namespace_imported = node_data.get("language") in _NAMESPACE_IMPORT_LANGUAGES
        if namespace_imported:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        # A whole file is a review candidate, never deletion-ready: see
        # ``REVIEW_ONLY_KINDS``. Confidence still ranks it.
        safe = False

        evidence = ["in_degree=0 (no files import this)"]
        if no_git_signal:
            evidence.append("No git history recorded for this file")
        elif commit_90d == 0:
            evidence.append("No commits in last 90 days")
        if self._dynamic_import_files and confidence <= RISK_CAP_CONFIDENCE:
            evidence.append("Package uses dynamic imports or runtime-resolved edges")
        risk_line = risk_evidence(risk_factors)
        if risk_line:
            evidence.append(risk_line)
        if namespace_imported:
            evidence.append(
                "Imported by namespace, not by file, so having no importer is weak evidence"
            )
        lines = self._file_line_count(node)
        if lines is None:
            evidence.append(_LINES_UNKNOWN)

        return DeadCodeFindingData(
            kind=DeadCodeKind.UNREACHABLE_FILE,
            file_path=node,
            symbol_name=None,
            symbol_kind=None,
            confidence=confidence,
            reason="File has no importers (in_degree=0)",
            last_commit_at=last_commit if isinstance(last_commit, datetime) else None,
            commit_count_90d=commit_90d,
            lines=lines,
            evidence=evidence,
            safe_to_delete=safe,
            primary_owner=primary_owner,
            age_days=age_days,
            risk_factors=list(risk_factors),
        )

    def _git_age_confidence(
        self, commit_90d: int, last_commit: Any, age_days: int | None
    ) -> float:
        """The git-age evidence ladder for a file no edge reaches."""
        if commit_90d != 0:
            return 0.4
        if last_commit:
            for days, confidence in _GIT_AGE_RUNGS:
                if self._is_old(last_commit, days=days):
                    return confidence
        if age_days is not None and age_days < 30:
            return 0.55  # Recently created â€” may be WIP
        return 0.7


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
            if not self._is_internal_candidate(node_data, dynamic_patterns, whitelist):
                continue
            file_path = node_data.get("file_path", "")
            sym_name = node_data.get("name", "")

            # Any inbound use, not only a call. A base class that is subclassed
            # rather than instantiated, a collaborator the container constructs,
            # and a handler named as a value rather than invoked each carry a
            # symbol-level edge of their own; reading only ``calls`` here
            # reported all three as unused. The set is shared with the
            # unused-export pass, so it also carries types this population can
            # never hold â€” a method or an interface is filtered out above.
            # ``new X(..)`` lands on X's constructor (``path::X::X``) when X
            # declares one, and constructing a type uses it.
            if self._has_inbound_use(node) or self._has_inbound_use(
                f"{file_path}::{sym_name}::{sym_name}"
            ):
                continue

            # Dispatch-table pattern: a private helper imported by name
            # into a sibling module and stored in a lookup dict
            # (``HANDLERS = {"python": _extract_python_heritage, ...}``).
            # The function is reached at runtime via dict lookup, so no
            # direct ``calls`` edge ever lands in the graph â€” but the
            # ``imports`` edge into its file carries the symbol name. If
            # any cross-file importer pulled this symbol by name,
            # something is actively referencing it; do not flag.
            if any(
                sym_name in imported or "*" in imported
                for imported in self._imported_names_by_edge(file_path)
            ):
                continue

            # Private symbols keep the standard 0.65 base confidence even if deprecated.
            # A private symbol has no external consumer by construction â€”
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
                    evidence=[f"No call, reference or override reaches '{sym_name}'"],
                    safe_to_delete=False,
                    risk_factors=list(path_risk_factors(file_path)),
                    **_symbol_span(node_data),
                    **self._git_fields(file_path),
                )
            )

        return findings

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

    def _is_internal_candidate(
        self, node_data: dict, dynamic_patterns: tuple[str, ...], whitelist: set[str]
    ) -> bool:
        """Whether a graph node is a private symbol the unused-internal pass may judge."""
        if node_data.get("node_type") != "symbol":
            return False
        # Rust: rustc's own `dead_code` lint already reports unused
        # private items, with the macro expansion and type information a
        # static graph cannot match. Nothing here it would not find first.
        if node_data.get("language") == "rust":
            return False
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
            return False
        file_path = node_data.get("file_path", "")
        if not file_path:
            return False
        file_data = self.graph.nodes.get(file_path, {})
        if file_data.get("is_test", False):
            return False
        if _is_fixture_path(file_path):
            return False
        if self._should_never_flag(file_path, whitelist):
            return False
        return self._is_judgeable_internal(node_data, dynamic_patterns)

    def _is_judgeable_internal(self, node_data: dict, dynamic_patterns: tuple[str, ...]) -> bool:
        """Whether a missing call edge says anything about this private symbol."""
        sym_name = node_data.get("name", "")
        if sym_name.startswith("__") and sym_name.endswith("__"):
            return False
        if sym_name in _ENTRY_POINT_SYMBOL_NAMES:
            return False
        # Namespace-path fragments (e.g. ``eShop.ClientApp``) and
        # non-callable kinds bypass the call-edge pass by design.
        if "." in sym_name:
            return False
        if node_data.get("kind") in _non_importable_kinds(node_data.get("language", "unknown")):
            return False
        # Non-callable type kinds can't have CALL edges; the call-graph
        # check this pass performs is meaningless for them (see
        # _UNCALLABLE_TYPE_KINDS). They remain covered by unused_export.
        if node_data.get("kind") in _UNCALLABLE_TYPE_KINDS:
            return False
        if is_contract_method(
            sym_name,
            node_data.get("kind"),
            node_data.get("language", "unknown"),
            modifiers=node_data.get("modifiers"),
        ):
            return False
        if self._name_matches_dynamic(sym_name, dynamic_patterns):
            return False

        # Framework-decorator skip â€” same shape as unused-export. A
        # private ``@PostConstruct``/``@EventListener``/``@Scheduled``
        # method is invoked by the container, not by a source call.
        decorators = node_data.get("decorators") or []
        meta = self._framework_meta_annotations(node_data.get("file_path", ""))
        return not (
            _is_framework_registered(decorators, meta) or _declared_deliberately_unused(decorators)
        )

    def _detect_zombie_packages(self, whitelist: set[str]) -> list[DeadCodeFindingData]:
        """Detect monorepo packages with no incoming inter_package edges.

        ``framework:`` predecessors (synthetic anchors added by
        ``framework_edges``) count as cross-package importers â€” TYPO3 / Django
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
            if not self._is_package_candidate(pkg, files, whitelist):
                continue
            if self._has_cross_package_importer(pkg, files):
                continue

            # A package total is only as known as its least-known file.
            counts = [
                self._file_line_count(f)
                for f in files
                if self.graph.nodes[f].get("node_type") != "symbol"
            ]
            known = [c for c in counts if c is not None]
            total_lines = sum(known) if len(known) == len(counts) else None
            evidence = [f"No inter-package imports into '{pkg}'"]
            if total_lines is None:
                evidence.append(_LINES_UNKNOWN)
            activity = self._package_git_activity(files)
            findings.append(
                DeadCodeFindingData(
                    kind=DeadCodeKind.ZOMBIE_PACKAGE,
                    file_path=pkg,
                    symbol_name=None,
                    symbol_kind=None,
                    confidence=0.5,
                    reason=f"Package '{pkg}' has no importers from other packages",
                    lines=total_lines,
                    evidence=evidence,
                    safe_to_delete=False,
                    risk_factors=list(path_risk_factors(pkg)),
                    **activity,
                )
            )

        return findings

    def _is_package_candidate(self, pkg: str, files: list[str], whitelist: set[str]) -> bool:
        """Whether a top-level directory is a code package the zombie pass may judge."""
        if pkg in whitelist:
            return False
        # Skip known non-package dirs (.github, .vscode, docs, ...)
        # and any other dotfile directory at the repo root.
        if pkg in _NEVER_PACKAGE_DIRS or pkg.startswith("."):
            return False
        # A folder is a package only when it declares itself one: a Next.js
        # ``app/``, a ``benchmarks/`` folder or a Go ``cmd/`` has no manifest
        # and nothing is meant to import it.
        if not self._has_package_manifest(pkg, files):
            return False
        # Tests are run, never imported, and so is a package that ships a
        # program: a CLI started by its shebang, a JVM ``main``, or a root
        # with a top-level ``main`` (Dart ``lib/main.dart``). A root alone is
        # not enough: a Rust ``lib.rs`` is a root and the library's surface.
        # One program file is enough, even a stray script: this errs toward
        # not calling a package dead. A build-run source set (``is_never_flag``)
        # counts as a test. Files only: a test file's symbols carry no test flag.
        files = [
            f
            for f in files
            if (nd := self.graph.nodes.get(f, {})).get("node_type") != "symbol"
            and not nd.get("is_test")
            and not nd.get("is_never_flag")
        ]
        if any(
            is_program(f, self._source_map.get(f, b""))
            or defines_main(self.graph, f)
            or (
                is_reachability_root(self.graph.nodes[f])
                and defines_main_function(self.graph, f)
            )
            for f in files
        ):
            return False
        # A real package contains at least one source file something could
        # import. Config and data (YAML, JSON, MD, TOML) is metadata, and a
        # folder of Dockerfiles, shell scripts and build files is run, never
        # imported.
        return any(
            self.graph.nodes.get(f, {}).get("language", "unknown")
            not in _DEAD_CODE_EXEMPT_LANGUAGES | _RUN_NOT_IMPORTED_LANGUAGES
            and not is_build_file(f)
            for f in files
        )

    def _has_package_manifest(self, pkg: str, files: list[str]) -> bool:
        """Whether *pkg* holds a package manifest of its own, on disk or indexed.

        A top-level folder whose manifest sits at the repository root (a
        Python package built by the root ``pyproject.toml``) is the repository
        itself, not a package of it, so it is never a candidate.
        """
        names = {f.partition("/")[2] for f in files if f.count("/") == 1}
        if self._repo_root is not None:
            with contextlib.suppress(OSError):
                names.update(os.listdir(Path(self._repo_root) / pkg))
        manifests = package_manifest_names()
        return any(n in manifests or n.endswith(_PROJECT_FILE_SUFFIXES) for n in names)

    def _has_cross_package_importer(self, pkg: str, files: list[str]) -> bool:
        """Whether anything outside *pkg* depends on a file inside it."""
        for f in files:
            for pred, _f, edge_data in self.graph.in_edges(f, data=True):
                # Committed together is history, not a dependency.
                if edge_data.get("edge_type") in _NON_DEPENDENCY_EDGES:
                    continue
                pred_str = str(pred)
                if pred_str.startswith("external:"):
                    # Third-party imports don't count as cross-package
                    # importers; framework: synthetic anchors do.
                    continue
                pred_parts = Path(pred_str).parts
                if len(pred_parts) > 0 and pred_parts[0] != pkg:
                    return True
        return False

    def _package_git_activity(self, files: list[str]) -> dict[str, Any]:
        """Latest commit, 90-day commits, most common owner and age across *files*."""
        pkg_last_commit: datetime | None = None
        pkg_total_commits_90d = 0
        pkg_owner: str | None = None
        owner_counts: dict[str, int] = {}
        for f in files:
            # git_meta_map values are plain dicts (see pipeline/phases/analysis.py);
            # use .get() not getattr() â€” getattr on a dict never finds arbitrary
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
        return {
            "last_commit_at": pkg_last_commit,
            "commit_count_90d": pkg_total_commits_90d,
            "primary_owner": pkg_owner,
            "age_days": pkg_age_days,
        }

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
