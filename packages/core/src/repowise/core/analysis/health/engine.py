"""HealthAnalyzer — thin orchestrator over walker → biomarkers → scorer.

Run sequence per file:

  1. Open the source bytes from ``ParsedFile.file_info.abs_path``.
  2. Walk the AST with ``complexity.walk_file_complexity`` → list of
     ``FunctionComplexity``.
  3. Build a ``FileContext`` (function metrics, git meta, dependents
     count, NLOC, test-file flag).
  4. Run all registered biomarkers via ``biomarkers.detect_all``.
  5. Score the file, attach per-finding impacts.
  6. Side effect: write ``max(ccn)`` into each Symbol's
     ``complexity_estimate`` so downstream consumers benefit.

Repo-level KPIs are computed from the final per-file metrics.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from ...ingestion.git_indexer.enrich import count_active_contributors
from ...ingestion.git_indexer.function_blame import (
    BlameIndex,
    distinct_commits_in_range,
)

# Package attribution lives in one place, shared with the traverser and with
# the `repowise update` backfill, so all three agree on what a package is.
from ...ingestion.package_roots import module_for as _module_for
from ...ingestion.package_roots import package_roots_from_paths as _package_roots
from ...ingestion.package_roots import scan_package_roots as _scan_package_roots
from ..graph_view import HasEdge, ImportEdgeView
from ..test_reachability import files_reached_by_tests
from .biomarkers import FileContext, detect_all
from .complexity import FileComplexity, FunctionComplexity, walk_file
from .coverage import is_test_file as _coverage_is_test_file
from .dataflow import FileDataflowCache
from .duplication import DuplicationReport
from .duplication.isolation import detect_clones_with_isolation as detect_clones
from .models import HealthFileMetricData, HealthFindingData, HealthReport, Severity
from .perf import (
    CallGraphIndex,
    PerfRanker,
    apply_perf_promotions,
    build_performance_opportunities,
    collect_blocking_io_under_lock,
    collect_centrality_gated,
    collect_crossfn_io_in_loop,
    link_performance_findings,
)
from .refactoring import (
    PerformancePlanPolicy,
    RefactoringContext,
    RefactoringSuggestion,
    detect_refactorings,
    performance_fix_suggestions,
    rank_suggestions,
)
from .refactoring.graph_signals import build_file_scc_index, build_methods_by_file
from .scoring import attach_impacts, compute_kpis, remap_severities, score_file
from .source_reader import SourceReader, disk_source_reader

log = structlog.get_logger(__name__)

# Bump when a change to this analyzer makes already-persisted health rows wrong
# — a new or removed biomarker, a changed attribution rule, a different finding
# shape. ``repowise update`` compares it against the value stored in state.json
# and re-scores on mismatch (see ``update_cmd.persistence.full_rescore_due``),
# instead of waiting out the 7-day decay timer.
#
# Reach, stated honestly: the gate is only consulted once an update reaches the
# incremental path, so this lands on the next update that has changed files. A
# repo with no new commits returns at the "already up to date" branch and picks
# the correction up on its next commit; workspace members and the hosted
# indexer do not run this path at all. That is the same reach the decay timer
# already has — this extends that trigger rather than adding a wider one.
#
# Deliberately *not* folded into ``config_fingerprint``: that fingerprint means
# "this repo's config content changed", and a workspace update answers drift in
# it by full-re-indexing every member repo. An analyzer change invalidates the
# scores, not the parse or the git index, so it routes to the re-score alone.
#
# Not a licence to move a calibrated scoring weight — those are frozen
# independently of this stamp.
#
# Current stamp: paired-test detection changed. ``_has_paired_test_file`` had
# ``test_<stem>.py`` hardcoded, so the prefix layout only ever matched Python;
# it now follows the file's own suffix, and ``<stem>_spec`` joins the suffix
# forms. Files that were counted untested and are not become tested, which
# moves untested-hotspot findings and the scores that carry them, on every
# language with a prefix or spec convention rather than Ruby alone.
HEALTH_ANALYZER_VERSION = 8

# Method-level smells that make the dataflow / Extract Method pass worthwhile.
# Only files carrying one of these get a CFG + def/use + reaching pass built.
_EXTRACT_METHOD_SOURCES = frozenset({"large_method", "brain_method", "complex_method"})


def _log_duplication_diagnostics(report: DuplicationReport) -> None:
    """Emit a debug line when a duplication guard fired.

    Skipped bundles / capped buckets are otherwise invisible — surfacing
    them explains why a repo produced fewer clone findings than expected
    (and confirms the issue-#341 hang guards are doing their job).
    """
    diag = report.diagnostics
    if not diag:
        return
    if any(
        diag.get(k)
        for k in (
            "skipped_minified",
            "skipped_token_cap",
            "window_budget_hit",
            "degenerate_buckets",
            "timed_out",
        )
    ):
        log.debug("health_duplication_limits", **diag)


def _read_source_lines(abs_path: str, read_source: SourceReader) -> list[str] | None:
    """Read a file's source as 1-indexed lines for the Extract Helper snippet.

    Failure-isolated: a read or decode error yields ``None`` (the detector then
    omits the snippet and keeps its line ranges), never an exception into the
    health pass. Decodes UTF-8 leniently since the snippet is for display, not
    re-parsing.
    """
    raw = read_source(abs_path)
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8", errors="replace")
    except (UnicodeError, AttributeError):
        return None
    return text.splitlines()


def _percentile_p80(counts: list[int]) -> int | None:
    """80th percentile of *counts* using the inclusive-lower convention
    already used by ``churn_percentile`` in ``enrich.compute_percentiles``.
    Returns ``None`` for an empty list.
    """
    if not counts:
        return None
    counts = sorted(counts)
    idx_p80 = min(len(counts) - 1, max(0, int(0.8 * len(counts))))
    return counts[idx_p80]


def _compute_repo_function_mod_p80(
    walked: list[tuple[Any, FileComplexity]],
    git_meta_map: dict[str, dict],
) -> int | None:
    """Compute the repo-wide 80th percentile of per-function modification counts.

    Uses the per-file ``BlameIndex`` produced by the FULL git tier. Returns
    ``None`` when blame is unavailable on every file (ESSENTIAL tier, or
    git indexing skipped entirely) — biomarkers treat ``None`` as the
    "no signal" outcome.
    """
    counts: list[int] = []
    for pf, fcx in walked:
        meta = git_meta_map.get(pf.file_info.path) or {}
        idx = meta.get("blame_index")
        if not isinstance(idx, BlameIndex) or not idx.lines:
            continue
        for fc in fcx.functions:
            mod_count = len(distinct_commits_in_range(idx, fc.start_line, fc.end_line))
            if mod_count > 0:
                counts.append(mod_count)
    return _percentile_p80(counts)


def _compute_repo_dependents_p80(parsed_files: list[Any], graph: Any) -> int | None:
    """Repo-wide 80th percentile of file-level in-degree (dependents).

    Restricted to files that actually have ≥1 dependent — this is the
    "top quintile of *connected* files", mirroring the mod-count p80
    convention (which only counts functions that were actually modified).
    Returns ``None`` when no graph is available or no file has dependents,
    in which case centrality-percentile gates fall back to their fixed
    floor. Used by ``brain_method`` so its centrality gate adapts to
    sparse-graph languages (TS/Rust) instead of assuming Python's denser
    import graph.
    """
    if graph is None:
        return None
    counts: list[int] = []
    for pf in parsed_files:
        path = pf.file_info.path
        if path not in graph:
            continue
        try:
            deg = int(graph.in_degree(path))
        except Exception:
            continue
        if deg > 0:
            counts.append(deg)
    return _percentile_p80(counts)


def _compute_repo_active_contributors(git_meta_map: dict[str, dict]) -> int | None:
    """Distinct non-bot contributors active in the repo's trailing 90 days.

    Derived from the per-author ``last_commit_ts`` timestamps already in
    ``top_authors_json`` — no extra git work. ``None`` = unknown (git
    skipped, or a pre-timestamp index); biomarkers then keep their
    historical behaviour rather than mis-gating on a phantom team size.
    """
    metas = [m for m in git_meta_map.values() if isinstance(m, dict)]
    if not metas:
        return None
    try:
        return count_active_contributors(metas)
    except Exception as exc:
        log.debug("health_active_contributors_failed", error=str(exc))
        return None


def _path_basenames(all_paths: set[str]) -> set[str]:
    """Final path components of *all_paths*, split on ``/`` only.

    A path matches ``other.endswith("/" + c) or other == c`` for a
    slash-free candidate filename ``c`` exactly when its ``/``-basename
    equals ``c``, so one precomputed basename set answers every
    ``_has_paired_test_file`` lookup. Splitting on ``/`` only (not ``\\``)
    preserves that equivalence for any non-POSIX path that slips in.
    """
    return {p.rsplit("/", 1)[-1] for p in all_paths}


_PASCAL_UNIT_SUFFIXES = frozenset({".pas", ".pp", ".dpr", ".dpk", ".lpr"})


def _has_paired_test_file(rel_path: str, path_basenames: set[str]) -> bool:
    """Heuristic: does any other file look like a test for *rel_path*?

    Cheap and conservative — looks for common test-file naming
    conventions paired with the same basename. *path_basenames* is the
    precomputed ``_path_basenames`` set for the analyzed file list.
    """
    p = Path(rel_path)
    stem = p.stem
    test_suffix = ".exs" if p.suffix == ".ex" else p.suffix
    candidates = {
        f"test_{stem}{test_suffix}",
        f"{stem}_test{test_suffix}",
        f"{stem}_spec{test_suffix}",
        f"{stem}.test.ts",
        f"{stem}.test.tsx",
        f"{stem}.test.js",
        f"{stem}.test.mts",
        f"{stem}.test.cts",
        f"{stem}.spec.ts",
        f"{stem}.spec.js",
        f"{stem}.spec.mts",
        f"{stem}.spec.cts",
    }
    if p.suffix.lower() in _PASCAL_UNIT_SUFFIXES:
        # Delphi/FPC's lowercase "u" unit-name prefix (uFoo.pas) has no
        # test-file convention of its own; real-world projects pair it with
        # a standalone console test program named Test<Foo>.dpr (the "u" is
        # dropped, the extension is .dpr since a runnable test program is a
        # project file, not a unit). Only a lowercase "u" is stripped -- a
        # stem that merely starts with capital "U" (Utils.pas) is a
        # different word, not this naming convention. Confirmed against a
        # real ~150-file Delphi codebase: uKeymap.pas <-> TestKeymap.dpr,
        # uANSIParser.pas <-> TestANSIParser.dpr, uConsoleBuffer.pas <->
        # TestConsoleBuffer.dpr, etc. -- src/tools/Test*.dpr, not next to
        # the unit.
        pascal_stem = stem[1:] if stem[:1] == "u" else stem
        candidates.add(f"Test{pascal_stem}.dpr")
    return not candidates.isdisjoint(path_basenames)


class HealthAnalyzer:
    """Pure-Python health analyzer. No LLM, no network."""

    def __init__(
        self,
        graph: Any,  # networkx.DiGraph
        git_meta_map: dict[str, dict] | None = None,
        parsed_files: list[Any] | None = None,
        coverage_map: dict[str, dict[str, Any]] | None = None,
        community_label_map: dict[str, str] | None = None,
        duplication_cache_dir: Any | None = None,
        repo_root: Any | None = None,
        source_reader: SourceReader | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}
        self.parsed_files = list(parsed_files or [])
        # Per-file coverage keyed by repo-relative POSIX path. Each value
        # is ``{line_coverage_pct, branch_coverage_pct, covered_lines,
        # total_coverable_lines}``. ``None``-equivalent files are simply
        # absent from the map.
        self.coverage_map = coverage_map or {}
        # Per-file community label keyed by repo-relative POSIX path,
        # populated from graph community detection by the orchestrator and
        # consumed only by the refactoring detectors. Distinct from the
        # ``module`` column, which is a path derived from package boundaries.
        self.community_label_map = community_label_map or {}
        # Directory for the duplication token/window cache (typically the
        # repo's ``.repowise``). None disables caching — the duplication
        # pass then re-tokenizes everything, exactly as before.
        self.duplication_cache_dir = duplication_cache_dir
        # Checkout root, used only to read package boundaries off disk. None
        # falls back to inferring them from the analyzed file list, which sees
        # only the manifests the traverser emitted.
        self.repo_root = repo_root
        # Every source read in the pass. Defaults to the working tree; a
        # revision comparison supplies bytes instead.
        self.read_source: SourceReader = source_reader or disk_source_reader
        self._package_roots_cache: set[str] | None = None
        self._tests_reach_cache: set[str] | None = None
        self._execution_graph_cache: CallGraphIndex | None = None

    def _execution_graph(self) -> CallGraphIndex | None:
        """Build the reliable execution index at most once per analysis."""
        if self.graph is not None and self._execution_graph_cache is None:
            self._execution_graph_cache = CallGraphIndex(self.graph)
        return self._execution_graph_cache

    def _files_reached_by_tests(self) -> set[str]:
        """Non-test files that some test file can execute into, per the call graph.

        The graph-backed half of "is this file tested". Computed once per
        analyzer over the whole file set (one multi-source walk, not one per
        file), because every ``_evaluate_file`` call asks the same question of
        the same graph.

        Inferred, and it over-claims: a call edge says control *can* reach the
        file, not that a given run did. Consumers must use it only as a floor -
        "something tests this" - never as a coverage quantity. See
        ``analysis.test_reachability``.
        """
        if self._tests_reach_cache is None:
            test_files = {pf.file_info.path for pf in self.parsed_files if pf.file_info.is_test}
            index = self._execution_graph()
            self._tests_reach_cache = files_reached_by_tests(index or CallGraphIndex(), test_files)
        return self._tests_reach_cache

    def _package_boundaries(self, analyzed_paths: set[str]) -> set[str]:
        """Package roots for this repo, decided once per analyzer.

        One answer for every file, so ``module`` is a property of the repo
        layout rather than of whichever code path happens to be running — the
        defect this replaced was four call sites disagreeing.

        Read off disk when a ``repo_root`` is known, because the analyzed file
        list only carries manifests the traverser could language-detect, and it
        drops ``go.mod``, ``pom.xml``, ``build.gradle``, ``Gemfile``,
        ``build.sbt`` and a dozen more. Falls back to the file list otherwise
        (in-memory callers and tests), which is the previous behaviour.
        """
        if self._package_roots_cache is not None:
            return self._package_roots_cache
        roots: set[str] | None = None
        if self.repo_root is not None:
            try:
                roots = _scan_package_roots(self.repo_root)
            except OSError as exc:
                # An unreadable tree must not fail the health pass; the file
                # list still gives the pre-existing answer.
                log.debug("health_package_root_scan_failed", error=str(exc))
        if roots is None:
            roots = _package_roots(analyzed_paths)
        self._package_roots_cache = roots
        return roots

    def analyze(
        self,
        config: dict | None = None,
        *,
        on_step: Any | None = None,
        changed_files: set[str] | list[str] | None = None,
        repo_function_mod_p80: int | None = None,
    ) -> HealthReport:
        """Analyze the configured parsed files.

        Pass *changed_files* (repo-relative POSIX paths) for incremental
        runs from ``repowise update`` — the engine still needs the full
        parsed-file set to build duplication context (clones cross
        files), but only files in *changed_files* contribute findings /
        metrics. The caller is responsible for upserting (not replacing)
        the result against the existing rows.

        *repo_function_mod_p80* overrides the repo-wide 80th percentile of
        per-function modification counts that gates the Function Hotspot
        biomarker. On an incremental run ``walked`` holds only the changed
        files, so deriving the percentile from it would bias the gate
        toward churn-heavy subsets (issue #1484) — the caller should pass
        the percentile computed over the full repo (from the persisted
        ``git_function_blame`` rollup) instead. ``None`` (the default)
        computes it from the walked set as before.
        """
        cfg = config or {}
        disabled: list[str] = list(cfg.get("disabled_biomarkers", ()))
        per_file_disabled: dict[str, set[str]] = cfg.get("per_file_disabled", {}) or {}
        repo_severity_overrides: dict[str, Severity] = cfg.get("severity_overrides", {}) or {}
        per_file_severity_overrides: dict[str, dict[str, Severity]] = (
            cfg.get("per_file_severity_overrides", {}) or {}
        )
        changed_set: set[str] | None = set(changed_files) if changed_files is not None else None

        # PageRank is optional — graph_builder.symbol_pagerank exists but
        # is symbol-level; we use file-level in-degree as the dependents
        # signal (cheap, deterministic, conservative).
        analyzed_paths = {pf.file_info.path for pf in self.parsed_files}
        path_basenames = _path_basenames(analyzed_paths)
        package_roots = self._package_boundaries(analyzed_paths)
        graph_view: HasEdge | None = ImportEdgeView(self.graph) if self.graph is not None else None

        # Duplication runs once, up-front, so each file biomarker can see
        # its clone list. Cheap when the repo is small; when disabled
        # explicitly we skip the work entirely. Even for incremental
        # runs the result stays repo-wide: a changed file's clone partners
        # may be unchanged files — passing changed_files lets the detector
        # splice its persisted pair index instead of recomputing it all.
        if "dry_violation" in disabled:
            dup_report = DuplicationReport()
        else:
            try:
                dup_report = detect_clones(
                    self.parsed_files,
                    self.git_meta_map,
                    cache_dir=self.duplication_cache_dir,
                    source_reader=self.read_source,
                    changed_files=changed_set,
                )
                _log_duplication_diagnostics(dup_report)
            except Exception as exc:
                log.debug("health_duplication_failed", error=str(exc))
                dup_report = DuplicationReport()

        disabled_refactorings: list[str] = list(cfg.get("disabled_refactorings", ()))
        refactoring_enabled: bool = bool(cfg.get("refactoring_enabled", True))
        refactoring_min_confidence: str | None = cfg.get("refactoring_min_confidence")
        # Repo-wide SCC index (import cycles), computed once and threaded into
        # each file's RefactoringContext so Break Cycle never recomputes it.
        file_scc_index = build_file_scc_index(self.graph)
        methods_by_file = build_methods_by_file(self.graph)
        findings: list[HealthFindingData] = []
        metrics: list[HealthFileMetricData] = []
        suggestions: list[RefactoringSuggestion] = []

        # Pre-walk every target so we can compute the repo-wide p80 of
        # per-function modification counts ONCE before any biomarker runs.
        # The walked list is reused by the per-file biomarker stage below.
        walked: list[tuple[Any, FileComplexity]] = []
        for pf in self.parsed_files:
            if changed_set is not None and pf.file_info.path not in changed_set:
                continue
            try:
                fcx = self._walk(pf)
            except Exception as exc:
                log.debug("health_walk_failed", path=pf.file_info.path, error=str(exc))
                fcx = FileComplexity(functions=[], classes=[])
            walked.append((pf, fcx))
            # Walk tick — the phase total counts each file twice (walk +
            # evaluate); see analyze_async.
            if on_step:
                on_step(pf.file_info.path)

        repo_fn_mod_p80 = (
            repo_function_mod_p80
            if repo_function_mod_p80 is not None
            else _compute_repo_function_mod_p80(walked, self.git_meta_map)
        )
        repo_dependents_p80 = _compute_repo_dependents_p80(self.parsed_files, self.graph)
        repo_active_contributors = _compute_repo_active_contributors(self.git_meta_map)

        # Cross-function N+1: augment perf_hits before the biomarker stage.
        self._apply_crossfn_perf(walked)
        # One shared dataflow service for the whole pass: the promotion pass
        # and the Extract Method detector below read the same lazily parsed
        # per-file object, so no file is parsed twice for dataflow.
        dataflow_cache = FileDataflowCache(self.read_source)
        # Dataflow promotion: mark advisory perf hits whose loop is provably
        # iteration-independent (runs after the graph passes so the
        # centrality-gated nested-loop hits are present to promote).
        apply_perf_promotions(walked, dataflow=dataflow_cache)

        for pf, fcx in walked:
            # Side-effect: bump Symbol.complexity_estimate when we can
            # match by enclosing line range. Symbols not matched keep
            # their default (1).
            self._populate_symbol_complexity(pf, fcx.functions)

            file_disabled = list(disabled)
            extra = per_file_disabled.get(pf.file_info.path)
            if extra:
                for name in extra:
                    if name not in file_disabled:
                        file_disabled.append(name)
            file_severity_overrides = dict(repo_severity_overrides)
            file_severity_overrides.update(per_file_severity_overrides.get(pf.file_info.path, {}))
            file_metric, file_findings, file_suggestions = self._evaluate_file(
                pf,
                fcx,
                path_basenames,
                package_roots,
                disabled=file_disabled,
                dup_report=dup_report,
                graph_view=graph_view,
                repo_function_mod_p80=repo_fn_mod_p80,
                repo_dependents_p80=repo_dependents_p80,
                repo_active_contributors_90d=repo_active_contributors,
                severity_overrides=file_severity_overrides or None,
                disabled_refactorings=disabled_refactorings,
                refactoring_enabled=refactoring_enabled,
                refactoring_min_confidence=refactoring_min_confidence,
                file_scc_index=file_scc_index,
                methods_by_file=methods_by_file,
                dataflow_cache=dataflow_cache,
            )
            metrics.append(file_metric)
            findings.extend(file_findings)
            suggestions.extend(file_suggestions)

            if on_step:
                on_step(pf.file_info.path)

        # KPIs are repo-wide; on an incremental run they would be biased
        # by the changed-files subset. Skip them in that case — the
        # ``persist`` step recomputes KPIs from the merged DB rows.
        if changed_set is None:
            hotspot_paths = {p for p, meta in self.git_meta_map.items() if self._is_hotspot(meta)}
            kpis = compute_kpis(metrics, hotspot_paths)
        else:
            kpis = {}

        self._mark_perf_entry_reachability(findings)
        link_performance_findings(findings)
        opportunities = build_performance_opportunities(findings)
        if refactoring_enabled and "performance_fix" not in disabled_refactorings:
            suggestions.extend(
                performance_fix_suggestions(
                    opportunities,
                    nloc_by_file={metric.file_path: metric.nloc for metric in metrics},
                    min_confidence=refactoring_min_confidence,
                )
            )
        suggestions = rank_suggestions(
            suggestions, centrality=self._refactoring_centrality(suggestions)
        )
        return HealthReport(
            repo_id="",
            analyzed_at=datetime.now(UTC),
            findings=findings,
            metrics=metrics,
            kpis=kpis,
            function_blame_rows=self._function_blame_rows(walked),
            refactoring_suggestions=suggestions,
            performance_plan_policy=PerformancePlanPolicy(
                enabled=refactoring_enabled and "performance_fix" not in disabled_refactorings,
                min_confidence=refactoring_min_confidence,
            ),
        )

    async def analyze_async(
        self,
        config: dict | None = None,
        *,
        on_step: Any | None = None,
        changed_files: set[str] | list[str] | None = None,
        max_workers: int | None = None,
        repo_function_mod_p80: int | None = None,
    ) -> HealthReport:
        """Parallel variant of :meth:`analyze` for large repos.

        Splits the per-file work across an ``asyncio.gather`` of
        ``asyncio.to_thread`` calls. Tree-sitter parsing releases the
        GIL, so this gives a real wall-clock win even on single-process
        Python — the 30s budget on a 3,000-file synthetic repo (plan §4
        P4.6) is met by this path.

        Duplication still runs once up-front (cross-file by nature), and
        the symbol-complexity write-back still runs on the main thread
        so ORM objects don't cross thread boundaries unexpectedly.

        *repo_function_mod_p80* overrides the repo-wide percentile that
        gates the Function Hotspot biomarker (see :meth:`analyze`); on an
        incremental run the caller passes the value computed over the full
        repo so the gate is not biased by the changed-files subset.
        """
        cfg = config or {}
        disabled: list[str] = list(cfg.get("disabled_biomarkers", ()))
        per_file_disabled: dict[str, set[str]] = cfg.get("per_file_disabled", {}) or {}
        repo_severity_overrides: dict[str, Severity] = cfg.get("severity_overrides", {}) or {}
        per_file_severity_overrides: dict[str, dict[str, Severity]] = (
            cfg.get("per_file_severity_overrides", {}) or {}
        )
        changed_set: set[str] | None = set(changed_files) if changed_files is not None else None

        analyzed_paths = {pf.file_info.path for pf in self.parsed_files}
        path_basenames = _path_basenames(analyzed_paths)
        package_roots = self._package_boundaries(analyzed_paths)
        graph_view: HasEdge | None = ImportEdgeView(self.graph) if self.graph is not None else None

        # Duplication is only consumed by the biomarker stage, so it can
        # overlap with the pre-walk instead of blocking it — on large
        # repos the scan takes seconds during which the progress bar
        # would otherwise sit at zero.
        dup_task: asyncio.Task | None = None
        if "dry_violation" not in disabled:
            dup_task = asyncio.ensure_future(
                asyncio.to_thread(
                    detect_clones,
                    self.parsed_files,
                    self.git_meta_map,
                    cache_dir=self.duplication_cache_dir,
                    source_reader=self.read_source,
                    changed_files=changed_set,
                )
            )

        target_files = [
            pf
            for pf in self.parsed_files
            if changed_set is None or pf.file_info.path in changed_set
        ]
        if not target_files:
            if dup_task is not None:
                dup_task.cancel()
            return HealthReport(
                repo_id="",
                analyzed_at=datetime.now(UTC),
                findings=[],
                metrics=[],
                kpis={},
            )

        # Pre-walk in worker threads so each task hands a list of
        # FunctionComplexity entries to the synchronous biomarker stage.
        # tree-sitter parsing releases the GIL → real parallelism here.
        workers = max(1, int(max_workers or os.cpu_count() or 4))
        semaphore = asyncio.Semaphore(workers)

        async def _one(pf: Any) -> tuple[Any, FileComplexity]:
            async with semaphore:
                try:
                    fcx = await asyncio.to_thread(self._walk, pf)
                except Exception as exc:
                    log.debug("health_walk_failed", path=pf.file_info.path, error=str(exc))
                    fcx = FileComplexity(functions=[], classes=[])
            # Walk tick — the phase total counts each file twice (walk +
            # evaluate) so the bar moves from the very first completed walk.
            if on_step:
                on_step(pf.file_info.path)
            return pf, fcx

        walked = await asyncio.gather(*[_one(pf) for pf in target_files])

        if dup_task is None:
            dup_report = DuplicationReport()
        else:
            try:
                dup_report = await dup_task
                _log_duplication_diagnostics(dup_report)
            except Exception as exc:
                log.debug("health_duplication_failed", error=str(exc))
                dup_report = DuplicationReport()
        repo_fn_mod_p80 = (
            repo_function_mod_p80
            if repo_function_mod_p80 is not None
            else _compute_repo_function_mod_p80(list(walked), self.git_meta_map)
        )
        repo_dependents_p80 = _compute_repo_dependents_p80(self.parsed_files, self.graph)
        repo_active_contributors = _compute_repo_active_contributors(self.git_meta_map)

        # Cross-function N+1: augment perf_hits before the biomarker stage.
        walked = list(walked)
        self._apply_crossfn_perf(walked)
        # One shared dataflow service per pass (see the sync path above).
        dataflow_cache = FileDataflowCache(self.read_source)
        # Dataflow promotion: mark advisory perf hits whose loop is provably
        # iteration-independent (after the graph passes populate the hits).
        apply_perf_promotions(walked, dataflow=dataflow_cache)

        disabled_refactorings: list[str] = list(cfg.get("disabled_refactorings", ()))
        refactoring_enabled: bool = bool(cfg.get("refactoring_enabled", True))
        refactoring_min_confidence: str | None = cfg.get("refactoring_min_confidence")
        file_scc_index = build_file_scc_index(self.graph)
        methods_by_file = build_methods_by_file(self.graph)
        findings: list[HealthFindingData] = []
        metrics: list[HealthFileMetricData] = []
        suggestions: list[RefactoringSuggestion] = []
        for pf, fcx in walked:
            self._populate_symbol_complexity(pf, fcx.functions)
            file_disabled = list(disabled)
            extra = per_file_disabled.get(pf.file_info.path)
            if extra:
                for name in extra:
                    if name not in file_disabled:
                        file_disabled.append(name)
            file_severity_overrides = dict(repo_severity_overrides)
            file_severity_overrides.update(per_file_severity_overrides.get(pf.file_info.path, {}))
            file_metric, file_findings, file_suggestions = self._evaluate_file(
                pf,
                fcx,
                path_basenames,
                package_roots,
                disabled=file_disabled,
                dup_report=dup_report,
                graph_view=graph_view,
                repo_function_mod_p80=repo_fn_mod_p80,
                repo_dependents_p80=repo_dependents_p80,
                repo_active_contributors_90d=repo_active_contributors,
                severity_overrides=file_severity_overrides or None,
                disabled_refactorings=disabled_refactorings,
                refactoring_enabled=refactoring_enabled,
                refactoring_min_confidence=refactoring_min_confidence,
                file_scc_index=file_scc_index,
                methods_by_file=methods_by_file,
                dataflow_cache=dataflow_cache,
            )
            metrics.append(file_metric)
            findings.extend(file_findings)
            suggestions.extend(file_suggestions)
            if on_step:
                on_step(pf.file_info.path)

        if changed_set is None:
            hotspot_paths = {p for p, meta in self.git_meta_map.items() if self._is_hotspot(meta)}
            kpis = compute_kpis(metrics, hotspot_paths)
        else:
            kpis = {}

        self._mark_perf_entry_reachability(findings)
        link_performance_findings(findings)
        opportunities = build_performance_opportunities(findings)
        if refactoring_enabled and "performance_fix" not in disabled_refactorings:
            suggestions.extend(
                performance_fix_suggestions(
                    opportunities,
                    nloc_by_file={metric.file_path: metric.nloc for metric in metrics},
                    min_confidence=refactoring_min_confidence,
                )
            )
        suggestions = rank_suggestions(
            suggestions, centrality=self._refactoring_centrality(suggestions)
        )
        return HealthReport(
            repo_id="",
            analyzed_at=datetime.now(UTC),
            findings=findings,
            metrics=metrics,
            kpis=kpis,
            function_blame_rows=self._function_blame_rows(walked),
            refactoring_suggestions=suggestions,
            performance_plan_policy=PerformancePlanPolicy(
                enabled=refactoring_enabled and "performance_fix" not in disabled_refactorings,
                min_confidence=refactoring_min_confidence,
            ),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refactoring_centrality(self, suggestions: list[RefactoringSuggestion]) -> dict[str, float]:
        """File-dependency centrality (importer in-degree) for each file a
        suggestion targets — the cheap, deterministic proxy ``rank`` blends
        with impact + blast radius. Empty when the health pass ran without a
        graph (the ranking then degrades to impact + blast only)."""
        if self.graph is None:
            return {}
        out: dict[str, float] = {}
        for s in suggestions:
            path = s.file_path
            if path in out or path not in self.graph:
                continue
            try:
                out[path] = float(self.graph.in_degree(path))
            except Exception:
                out[path] = 0.0
        return out

    def _apply_crossfn_perf(self, walked: list[tuple[Any, FileComplexity]]) -> None:
        """Run the graph-dependent perf passes over the walked files, in place.

        Four sources of extra ``perf_hits``, all sharing one
        :class:`CallGraphIndex` (built once over reliable execution edges):

          1. cross-function ``io_in_loop`` / N+1 (PR4);
          2. cross-function ``blocking_io_under_lock`` (Phase 7b) — the lock→I/O
             reachability case;
          3. the centrality-gated ``nested_loop_quadratic`` / ``hot_path_sync_io``
             markers (Phase 7b), generated from the walker's per-function facts
             ONLY for a hot function, via the :class:`PerfRanker`.

        Each is appended onto the matching file's ``perf_hits`` in place so the
        biomarkers handle every case through one path. Failure-isolated and never
        blocks the report. The cross-function passes are a no-op without a graph;
        the centrality-gated pass ALWAYS runs — without a graph nothing is
        hot, so it emits nothing (precision-first: we never ship a
        centrality-gated marker we cannot establish centrality for).
        """
        try:
            index = self._execution_graph()
            by_file: dict[str, list] = {}
            if self.graph is not None and index is not None:
                for src in (
                    collect_crossfn_io_in_loop(walked, self.graph, index=index),
                    collect_blocking_io_under_lock(walked, self.graph, index=index),
                ):
                    for path, hits in src.items():
                        by_file.setdefault(path, []).extend(hits)
            ranker = PerfRanker(index)
            for path, hits in collect_centrality_gated(walked, ranker).items():
                by_file.setdefault(path, []).extend(hits)
            for _pf, fcx in walked:
                extra = by_file.get(_pf.file_info.path)
                if extra:
                    fcx.perf_hits = [*fcx.perf_hits, *extra]
        except Exception as exc:
            log.debug("health_crossfn_perf_failed", error=str(exc))
            return

    def _mark_perf_entry_reachability(self, findings: list[HealthFindingData]) -> None:
        """Stamp reliable entry reachability without walking once per row."""
        index = self._execution_graph()
        if index is None or self.graph is None:
            return
        entry_files: set[str] = set()
        try:
            for node_id, data in self.graph.nodes(data=True):
                if data.get("node_type") == "file" and data.get("is_entry_point"):
                    entry_files.add(node_id)
        except Exception:
            return
        seeds = {symbol for path in entry_files for symbol in index.declares.get(path, ())}
        if not seeds:
            return
        reachable = index.forward_reachable(seeds)
        for finding in findings:
            if finding.dimension != "performance":
                continue
            path = finding.details.get("path")
            if isinstance(path, list) and path:
                finding.details["reliable_entry_reachability"] = path[0] in reachable

    def _function_blame_rows(self, walked: list[tuple[Any, FileComplexity]]) -> list[dict]:
        """Build the per-function blame rollup from the walked files + the
        FULL-tier blame indexes attached to ``git_meta_map``.

        Cheap (reads the already-materialised blame index; no extra git) and
        failure-isolated so a rollup hiccup never breaks the health report.
        Returns an empty list on the ESSENTIAL tier (no blame indexes).
        """
        try:
            from .function_blame_rollup import build_function_blame_rows

            return build_function_blame_rows(
                list(walked), self.git_meta_map, now_ts=int(time.time())
            )
        except Exception as exc:
            log.debug("function_blame_rollup_failed", error=str(exc))
            return []

    def _walk(self, pf: Any) -> FileComplexity:
        path = pf.file_info.abs_path
        language = pf.file_info.language
        source = self.read_source(path)
        if source is None:
            return FileComplexity(functions=[], classes=[])
        if language == "sql":
            # SQL has no tree-sitter grammar here; the sqlglot-backed walker
            # produces routine CCN + the sql_* smell hits instead.
            from .sql_complexity import walk_sql_file

            return walk_sql_file(pf.file_info, source)
        return walk_file(path, language, source)

    def _extract_method_analyses(
        self,
        pf: Any,
        findings: list[HealthFindingData],
        dataflow_cache: FileDataflowCache | None = None,
    ) -> list[Any]:
        """Dataflow analyses for the Extract Method detector, gated to files
        that already carry a method-level smell.

        Building a CFG + def/use + reaching definitions is only useful where a
        ``large_method`` / ``brain_method`` / ``complex_method`` finding fired,
        so the dataflow pass runs for that small subset of files only --
        everything else pays nothing. The shared *dataflow_cache* means a file
        the promotion pass already analyzed is not parsed again here. Degrades
        to ``[]`` on any read or analysis failure; the detector then yields no
        suggestion.
        """
        if not any(getattr(f, "biomarker_type", "") in _EXTRACT_METHOD_SOURCES for f in findings):
            return []
        cache = dataflow_cache if dataflow_cache is not None else FileDataflowCache(self.read_source)
        return cache.get(pf.file_info.abs_path, pf.file_info.language).flagged_analyses()

    def _populate_symbol_complexity(self, pf: Any, fc_list: list[FunctionComplexity]) -> None:
        if not fc_list:
            return
        # Index function metrics by (start_line, end_line) for fast lookup.
        by_range = {(fc.start_line, fc.end_line): fc for fc in fc_list}
        by_name = {fc.name: fc for fc in fc_list}
        for sym in pf.symbols:
            fc = by_range.get((sym.start_line, sym.end_line)) or by_name.get(sym.name)
            if fc is None:
                continue
            # Cap at the ORM Integer; CCN beyond ~10k is implausible.
            sym.complexity_estimate = int(min(fc.ccn, 9999))

    def _evaluate_file(
        self,
        pf: Any,
        fcx: FileComplexity,
        path_basenames: set[str],
        package_roots: set[str],
        *,
        disabled: list[str],
        dup_report: DuplicationReport,
        graph_view: HasEdge | None = None,
        repo_function_mod_p80: int | None = None,
        repo_dependents_p80: int | None = None,
        repo_active_contributors_90d: int | None = None,
        severity_overrides: dict[str, Severity] | None = None,
        disabled_refactorings: list[str] | None = None,
        refactoring_enabled: bool = True,
        refactoring_min_confidence: str | None = None,
        file_scc_index: dict[str, tuple[str, ...]] | None = None,
        methods_by_file: dict[str, tuple[str, ...]] | None = None,
        dataflow_cache: FileDataflowCache | None = None,
    ) -> tuple[HealthFileMetricData, list[HealthFindingData], list[RefactoringSuggestion]]:
        file_path = pf.file_info.path

        fc_list = fcx.functions
        # SQL routine metrics are text-counted and defect-uncalibrated; they
        # exist for symbol stamping and the sql_high_complexity marker
        # (maintainability). Keeping them out of function_metrics keeps the
        # calibrated method biomarkers (defect dimension) from firing on SQL.
        fn_metrics: dict[str, FunctionComplexity] = (
            {} if pf.file_info.language == "sql" else {fc.name: fc for fc in fc_list}
        )
        max_ccn = max((fc.ccn for fc in fc_list), default=1)
        max_nesting = max((fc.max_nesting for fc in fc_list), default=0)
        nloc = fcx.file_nloc

        dependents_count = 0
        if self.graph is not None and file_path in self.graph:
            try:
                dependents_count = int(self.graph.in_degree(file_path))
            except Exception:
                dependents_count = 0

        cov = self.coverage_map.get(file_path)
        if cov is None:
            cov = self.coverage_map.get(file_path.replace("\\", "/"))
        line_cov = cov.get("line_coverage_pct") if cov else None
        branch_cov = cov.get("branch_coverage_pct") if cov else None
        covered_lines: set[int] = set(cov.get("covered_lines") or ()) if cov else set()
        total_coverable_lines = int(cov.get("total_coverable_lines", 0)) if cov else 0

        clones = dup_report.pairs_by_file.get(file_path, [])
        dup_pct = dup_report.duplication_pct.get(file_path)

        # The enclosing package root, falling back to the top-level directory
        # when the repo has no nested packages.
        module = _module_for(file_path, package_roots)

        file_git_meta = self.git_meta_map.get(file_path, {}) or {}
        blame_idx_obj = file_git_meta.get("blame_index")
        blame_index = blame_idx_obj if isinstance(blame_idx_obj, BlameIndex) else None

        ctx = FileContext(
            file_path=file_path,
            language=pf.file_info.language,
            nloc=nloc,
            # ``pf.file_info.is_test`` is the decision ingestion already made
            # for this file, with its language in hand — read it rather than
            # re-derive from the path string (#1103). The coverage check stays
            # because it also sniffs framework imports out of the source, which
            # a path cannot tell you.
            has_test_file=_has_paired_test_file(file_path, path_basenames)
            or pf.file_info.is_test
            or _coverage_is_test_file(file_path)
            or fcx.has_inline_tests,
            # Kept separate from ``has_test_file`` on purpose. That flag means
            # "a file named like this one's test exists"; this one means "the
            # call graph records a test reaching this file". They disagree
            # often, and collapsing them would leave no way to say which signal
            # answered, or that one of them over-claims.
            reached_by_tests=file_path in self._files_reached_by_tests(),
            module=module,
            function_metrics=fn_metrics,
            class_metrics=fcx.classes,
            git_meta=file_git_meta,
            dependents_count=dependents_count,
            repo_dependents_p80=repo_dependents_p80,
            pagerank_score=0.0,
            line_coverage_pct=line_cov,
            branch_coverage_pct=branch_cov,
            covered_lines=covered_lines,
            total_coverable_lines=total_coverable_lines,
            clones=list(clones),
            duplication_pct=dup_pct,
            graph_view=graph_view,
            blame_index=blame_index,
            repo_function_mod_p80=repo_function_mod_p80,
            repo_active_contributors_90d=repo_active_contributors_90d,
            error_handling_hits=fcx.error_handling_hits,
            perf_hits=fcx.perf_hits,
            io_boundary_names=set(fcx.io_boundary_names),
        )

        biomarker_results = detect_all(ctx, disabled=disabled)
        biomarker_results = remap_severities(biomarker_results, severity_overrides)
        scores, deductions = score_file(biomarker_results)
        findings = attach_impacts(biomarker_results, deductions)
        for f in findings:
            f.file_path = file_path

        # The overall surfaced score stays == the defect dimension (no blend
        # yet); the per-dimension scores ride alongside it, additively.
        defect_score = scores["defect"]
        maint_score = scores["maintainability"]
        perf_score = scores["performance"]
        metric = HealthFileMetricData(
            file_path=file_path,
            score=round(defect_score, 2),
            max_ccn=max_ccn,
            max_nesting=max_nesting,
            nloc=nloc,
            # The stored field answers "does something test this file" - that is
            # how the MCP payload documents it and how every UI renders it
            # ("has tests" / "untested"). So it carries the union, not just the
            # naming convention. Keeping it filename-only left the file table
            # labelling a file untested while ``untested_hotspot`` stayed
            # silent about it, which is the same disagreement issue #1740 is
            # about, one layer further out. The two inputs stay separable on
            # ``FileContext`` and in the biomarker's ``details``.
            has_test_file=ctx.has_test_file or ctx.reached_by_tests,
            module=module,
            line_coverage_pct=line_cov,
            branch_coverage_pct=branch_cov,
            duplication_pct=dup_pct,
            defect_score=round(defect_score, 2),
            maintainability_score=(round(maint_score, 2) if maint_score is not None else None),
            performance_score=(round(perf_score, 2) if perf_score is not None else None),
        )

        # Refactoring layer: reuse the data just computed (class cohesion
        # components + this file's findings) to emit structured suggestions.
        # Fault-isolated per detector; degrades to [] on any missing signal.
        # Disabled outright from config => skip the whole pass (and its
        # dataflow build) for this file.
        if not refactoring_enabled:
            return metric, findings, []
        rctx = RefactoringContext(
            file_path=file_path,
            language=pf.file_info.language,
            nloc=nloc,
            classes=fcx.classes,
            findings=findings,
            dependents_count=dependents_count,
            clones=list(clones),
            community_label_map=self.community_label_map,
            graph=self.graph,
            file_scc=(file_scc_index or {}).get(file_path),
            file_methods=(
                methods_by_file.get(file_path, ()) if methods_by_file is not None else None
            ),
            function_analyses=self._extract_method_analyses(pf, findings, dataflow_cache),
            blame_index=blame_index,
            # Source is threaded only for clone-bearing files (the Extract Helper
            # detector's snippet). Reading it unconditionally would put a
            # repo-sized read back into the per-file path; gating on clones keeps
            # it proportional to the small set of files that actually carry one.
            source_lines=(
                _read_source_lines(pf.file_info.abs_path, self.read_source) if clones else None
            ),
        )
        suggestions = detect_refactorings(
            rctx,
            disabled=disabled_refactorings or (),
            min_confidence=refactoring_min_confidence,
        )
        return metric, findings, suggestions

    def _is_hotspot(self, meta: dict | object) -> bool:
        if isinstance(meta, dict):
            return bool(meta.get("is_hotspot", False))
        return bool(getattr(meta, "is_hotspot", False))
