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
import functools
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import structlog

from ...code_origin import CodeOrigin, code_origin
from ...git_refs import remote_name
from ...ingestion.git_indexer.enrich import count_active_contributors
from ...ingestion.git_indexer.function_blame import (
    BlameIndex,
    distinct_commits_in_range,
)

# Package attribution lives in one place, shared with the traverser and with
# the `repowise update` backfill, so all three agree on what a package is.
from ...ingestion.package_roots import module_for as _module_for  # noqa: F401 (re-export)
from ...ingestion.package_roots import package_roots_from_paths as _package_roots
from ...ingestion.package_roots import scan_package_roots as _scan_package_roots
from ...installed_components import is_installed_component
from ..dead_code.file_reachability import file_dependency_neighbors
from ..execution_roles import ExecutionRoles
from ..graph_view import ImportEdgeView
from .asserts.lexicon import AssertVocabulary
from .asserts.oracle_reach import collect_cross_file_oracles
from .complexity import FileComplexity, FunctionComplexity, walk_file
from .coverage_actions import paired_test_index
from .crossfn_perf import CrossFunctionPerfStage
from .dataflow import FileDataflowCache
from .duplication import ClonePair, DuplicationReport
from .duplication.detector import clone_ranges, clones_against_index, union_line_count
from .duplication.isolation import detect_clones_with_isolation as detect_clones
from .file_evaluator import FileEvaluator, _read_source_lines, _RunConfig
from .file_evaluator import _mark_deepest_block as _mark_deepest_block
from .file_evaluator import _mark_function_facts as _mark_function_facts
from .file_evaluator import walked_functions as walked_functions
from .models import HealthFileMetricData, HealthFindingData, HealthReport
from .perf import (
    CallGraphIndex,
    build_performance_opportunities,
    link_performance_findings,
)
from .ranking import deduction_by_path
from .refactoring import (
    PerformancePlanPolicy,
    RefactoringSuggestion,
    performance_fix_suggestions,
    rank_suggestions,
)
from .refactoring.graph_signals import (
    build_file_scc_index,
    build_methods_by_file,
    build_split_partial_types,
)
from .repo_tests import RepoTestIndex
from .repo_tests import _has_paired_test_file as _has_paired_test_file
from .repo_tests import _path_basenames as _path_basenames
from .scope import scores_language
from .scoring import compute_kpis
from .source_reader import SourceReader, disk_source_reader
from .walk_cache import HealthWalkCache

if TYPE_CHECKING:
    from ..function_test_map import FunctionTests

log = structlog.get_logger(__name__)

# Bump when a change to this analyzer makes already-persisted health rows wrong
# — a new or removed biomarker, a changed attribution rule, a different finding
# shape. ``repowise update`` compares it against the value stored in state.json
# and re-scores on mismatch (see ``update_cmd.persistence.full_rescore_due``),
# instead of waiting out the 7-day decay timer.
#
# Reach: a single-repo ``repowise update`` checks it before the "already up to
# date" return, so a checkout with no new commits re-scores on its next update
# too, from the graph it re-parses, with no model call. Workspace members and
# the hosted indexer do not run this path at all.
#
# Deliberately *not* folded into ``config_fingerprint``: that fingerprint means
# "this repo's config content changed", and a workspace update answers drift in
# it by full-re-indexing every member repo. An analyzer change invalidates the
# scores, not the parse or the git index, so it routes to the re-score alone.
#
# Not a licence to move a calibrated scoring weight — those are frozen
# independently of this stamp.
#
# v43: the code-origin classifier moved for several path shapes: a nested
# ``tools/`` folder is production (only a repo-root or engineering ``tools/``
# stays tooling), Python ``_vendor/`` folders and Salesforce static-resource
# libraries read as vendored, and prefixed migration folders under ``db/`` read
# as migrations. Stored origins decide health and dead-code scope, so those
# files re-score.
#
# Earlier bumps and their reasons: ``git log -S"HEALTH_ANALYZER_VERSION =" -p``
# on this file.
HEALTH_ANALYZER_VERSION = 43


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


def _partner(file_path: str, pair: ClonePair) -> str:
    """The other file of *pair*: *file_path* itself for a clone inside it."""
    return pair.file_b if pair.file_a == file_path else pair.file_a


def _pct_of_kept(
    file_path: str, clones: list[ClonePair], kept: list[ClonePair], dup_pct: float | None
) -> float | None:
    """*dup_pct* scaled to the lines the *kept* clones still cover."""
    if len(kept) == len(clones) or not dup_pct:
        return dup_pct
    covered = union_line_count(clone_ranges(file_path, clones))
    share = union_line_count(clone_ranges(file_path, kept)) / covered if covered else 0.0
    return round(dup_pct * share, 2)


#: Partner files whose lines one analysis pass keeps (``_repo_lines``).
_PARTNER_LINES_CACHE = 256


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


def _full_repo_p80(
    value: int | None, changed_files: object, override: int | None
) -> int | None:
    """*value*, but only when this run is entitled to publish it repo-wide.

    An incremental run walks the changed files alone, so the percentile it
    derives describes a churn-heavy subset and must never be stored as the
    repo's. A run that was handed an override did not measure anything either.
    """
    if changed_files is not None or override is not None:
        return None
    return value


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


def _dependents_count(graph: Any, path: str) -> int:
    """Distinct files that use *path* in code: co-change is history, and a Go
    package sibling or a synthetic header pair edge uses nothing."""
    return len(set(file_dependency_neighbors(graph, path, incoming=True, uses_only=True)))


def _compute_repo_dependents_p80(dependents: dict[str, int]) -> int | None:
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
    return _percentile_p80([deg for deg in dependents.values() if deg > 0])


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
        stored_blame_findings: dict[str, list[Any]] | None = None,
        file_dependents: dict[str, int] | None = None,
        tested_files: set[str] | frozenset[str] | None = None,
        walk_cache: HealthWalkCache | None = None,
        package_roots: set[str] | None = None,
        clone_index_dir: Any | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}
        self.parsed_files = list(parsed_files or [])
        self._abs_paths = {pf.file_info.path: pf.file_info.abs_path for pf in self.parsed_files}
        # Clone partners' lines, shared by the test-clone gate and Extract
        # Helper's reuse check. Bounded: partners cluster, a pass reads few.
        self._repo_lines = functools.lru_cache(maxsize=_PARTNER_LINES_CACHE)(self._read_repo_lines)
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
        # The walk result for a file is a function of its bytes, its language
        # and the walker's version, so it is kept beside the index like the
        # parse trees and the clone windows are. Absent without a cache dir.
        # A *walk_cache* passed in is the caller's: it loads and saves it, so
        # several passes (both sides of a comparison) share one file.
        self._owns_walk_cache = walk_cache is None
        self._walk_cache: HealthWalkCache | None = walk_cache or (
            HealthWalkCache(duplication_cache_dir, HEALTH_ANALYZER_VERSION)
            if duplication_cache_dir is not None
            else None
        )
        # Checkout root, used only to read package boundaries off disk. None
        # falls back to inferring them from the analyzed file list, which sees
        # only the manifests the traverser emitted.
        self.repo_root = repo_root
        # Origins are decided in ``_walk``, which holds the bytes, and kept by
        # path: the walk cache keys on content alone, so a path-dependent
        # answer cannot live on the cached walk.
        self._project_name: str | None = None
        self._origins: dict[str, CodeOrigin] = {}
        self._installed_ui_dirs: dict[str, tuple[PurePosixPath, ...]] = {}
        # Every source read in the pass. Defaults to the working tree; a
        # revision comparison supplies bytes instead.
        self.read_source: SourceReader = source_reader or disk_source_reader
        # Stored ``BLAME_MARKERS`` findings by path, replayed for a file scored
        # without a blame index (a re-score from stored git metadata) so the
        # pass does not delete what it cannot recompute.
        self.stored_blame_findings = stored_blame_findings or {}
        # An index's ``.repowise`` for a pass over a few files: their clones
        # are spliced against the index's pair state (read-only) so partners
        # this pass never reads still count. ``clone_source`` says where the
        # last pass's clones came from: "index", "analyzed_files" (only among
        # the files it read), or None when duplication did not run.
        self.clone_index_dir = clone_index_dir
        self.clone_source: str | None = None
        # Dependents counts to gate centrality on instead of this graph's. A
        # revision comparison passes the other side's, so a file crossing a
        # centrality gate inside a partial graph is not read as a code change.
        self._file_dependents_override = file_dependents
        # Files the index records as tested (paired or reached), for a pass holding no tests.
        self._tested_files = frozenset(tested_files or ())
        # Supplied by a caller that already knows the roots its files sit under.
        self._package_roots_cache: set[str] | None = package_roots
        self._execution_graph_cache: CallGraphIndex | None = None
        self.test_index = RepoTestIndex(self.graph, self.parsed_files, self._execution_graph)
        self.perf_stage = CrossFunctionPerfStage(
            self.graph, self._execution_graph, self._origins, self.parsed_files, self.read_source
        )

    def file_dependents(self) -> dict[str, int]:
        """Each analysed file's dependents count, as the centrality gates read it."""
        if self._file_dependents_override is not None:
            return dict(self._file_dependents_override)
        if self.graph is None:
            return {}
        return {
            pf.file_info.path: _dependents_count(self.graph, pf.file_info.path)
            for pf in self.parsed_files
            if pf.file_info.path in self.graph
        }

    def _dependents(self, path: str) -> int:
        override = self._file_dependents_override
        if override is not None and path in override:
            return override[path]
        if self.graph is None or path not in self.graph:
            return 0
        return _dependents_count(self.graph, path)

    def _execution_graph(self) -> CallGraphIndex | None:
        """Build the reliable execution index at most once per analysis."""
        if self.graph is not None and self._execution_graph_cache is None:
            self._execution_graph_cache = CallGraphIndex(self.graph)
        return self._execution_graph_cache

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
        timings: Any | None = None,
        duplication_files: set[str] | None = None,
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

        *timings* is a phase table; each stage of this pass records an
        ``analysis.health.*`` row so a slow pass names the stage.

        *duplication_files* narrows the clone detector's incremental splice to
        the files whose bytes changed. It defaults to *changed_files*, which on
        an update also carries the performance closure: files re-walked for
        their call paths, whose tokens did not move and whose clone pairs are
        already in the persisted index.
        """
        from repowise.core.pipeline.phase_timing import timed

        run = _RunConfig.read(config)
        changed_set = set(changed_files) if changed_files is not None else None
        dup_report = self._duplication(
            run,
            set(duplication_files) if duplication_files is not None else changed_set,
            timings,
        )
        with timed(timings, "analysis.health.walk"):
            walked = self._walk_files(self._target_files(changed_set), run.vocab, on_step)
        return self._score(
            run,
            walked,
            dup_report,
            changed_files=changed_files,
            repo_function_mod_p80=repo_function_mod_p80,
            on_step=on_step,
            timings=timings,
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
        run = _RunConfig.read(config)
        changed_set = set(changed_files) if changed_files is not None else None
        # Duplication is only consumed by the biomarker stage, so it can
        # overlap with the pre-walk instead of blocking it — on large
        # repos the scan takes seconds during which the progress bar
        # would otherwise sit at zero.
        dup_task = (
            None
            if "dry_violation" in run.disabled
            else asyncio.ensure_future(asyncio.to_thread(self._duplication, run, changed_set, None))
        )
        target_files = self._target_files(changed_set)
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
        walked = await self._walk_files_async(target_files, run.vocab, on_step, max_workers)
        return self._score(
            run,
            walked,
            await dup_task if dup_task is not None else DuplicationReport(),
            changed_files=changed_files,
            repo_function_mod_p80=repo_function_mod_p80,
            on_step=on_step,
            timings=None,
        )

    def _duplication(
        self, run: _RunConfig, changed: set[str] | None, timings: Any | None
    ) -> DuplicationReport:
        """Clone pairs for the pass, found once up front so each file's
        biomarkers see their clone list; empty when disabled or on failure.

        Repo-wide even for an incremental run, since a changed file's clone
        partners may be unchanged files: *changed* lets the detector splice
        its persisted pair index instead of recomputing it all.
        """
        from repowise.core.pipeline.phase_timing import timed

        self.clone_source = None
        # An empty splice set: no file whose clone list a biomarker reads changed.
        if "dry_violation" in run.disabled or changed == set():
            return DuplicationReport()
        if self.clone_index_dir is not None:
            report = self._clones_against_index()
            if report is not None:
                self.clone_source = "index"
                return report
        self.clone_source = "analyzed_files"
        try:
            with timed(timings, "analysis.health.duplication"):
                report = detect_clones(
                    self.parsed_files,
                    self.git_meta_map,
                    cache_dir=self.duplication_cache_dir,
                    source_reader=self.read_source,
                    changed_files=changed,
                )
            _log_duplication_diagnostics(report)
            return report
        except Exception as exc:
            log.debug("health_duplication_failed", error=str(exc))
            return DuplicationReport()

    def _clones_against_index(self) -> DuplicationReport | None:
        # A missing, stale or truncated clone state is ``None`` without raising,
        # so anything raised here is a defect: say so at warning rather than let
        # the files-only fallback pass for the expected no-index case.
        try:
            report = clones_against_index(
                self.parsed_files,
                self.git_meta_map,
                Path(self.clone_index_dir),
                source_reader=self.read_source,
            )
        except Exception as exc:
            log.warning("health_index_clones_failed", error=repr(exc))
            return None
        return report

    def _target_files(self, changed_set: set[str] | None) -> list[Any]:
        """The parsed files this pass scores: only the changed ones on an incremental run."""
        return [
            pf
            for pf in self.parsed_files
            if (changed_set is None or pf.file_info.path in changed_set)
            and scores_language(pf.file_info.language)
        ]

    def _walk_or_empty(self, pf: Any, vocab: AssertVocabulary) -> FileComplexity:
        try:
            return self._walk(pf, vocab)
        except Exception as exc:
            log.debug("health_walk_failed", path=pf.file_info.path, error=str(exc))
            return FileComplexity(functions=[], classes=[])

    def _walk_files(
        self, targets: list[Any], vocab: AssertVocabulary, on_step: Any | None
    ) -> list[tuple[Any, FileComplexity]]:
        """Walk every target before any biomarker runs, so the repo-wide
        percentiles are computed once over the whole walked set."""
        self._load_walk_cache()
        walked: list[tuple[Any, FileComplexity]] = []
        for pf in targets:
            walked.append((pf, self._walk_or_empty(pf, vocab)))
            # Walk tick — the phase total counts each file twice (walk +
            # evaluate).
            if on_step:
                on_step(pf.file_info.path)
        self._save_walk_cache()
        return walked

    async def _walk_files_async(
        self,
        targets: list[Any],
        vocab: AssertVocabulary,
        on_step: Any | None,
        max_workers: int | None,
    ) -> list[tuple[Any, FileComplexity]]:
        """:meth:`_walk_files` in worker threads: tree-sitter parsing releases
        the GIL, so this is real parallelism."""
        self._load_walk_cache()
        semaphore = asyncio.Semaphore(max(1, int(max_workers or os.cpu_count() or 4)))

        async def _one(pf: Any) -> tuple[Any, FileComplexity]:
            async with semaphore:
                fcx = await asyncio.to_thread(self._walk_or_empty, pf, vocab)
            # Walk tick — the phase total counts each file twice (walk +
            # evaluate) so the bar moves from the very first completed walk.
            if on_step:
                on_step(pf.file_info.path)
            return pf, fcx

        walked = list(await asyncio.gather(*[_one(pf) for pf in targets]))
        self._save_walk_cache()
        return walked

    def _score(
        self,
        run: _RunConfig,
        walked: list[tuple[Any, FileComplexity]],
        dup_report: DuplicationReport,
        *,
        changed_files: set[str] | list[str] | None,
        repo_function_mod_p80: int | None,
        on_step: Any | None,
        timings: Any | None,
    ) -> HealthReport:
        """Everything after the walk, shared by :meth:`analyze` and
        :meth:`analyze_async` so the two paths cannot drift apart."""
        from repowise.core.pipeline.phase_timing import timed

        with timed(timings, "analysis.health.repo_stats"):
            fn_mod_p80 = (
                repo_function_mod_p80
                if repo_function_mod_p80 is not None
                else _compute_repo_function_mod_p80(walked, self.git_meta_map)
            )
            full_repo_fn_mod_p80 = _full_repo_p80(
                fn_mod_p80, changed_files, repo_function_mod_p80
            )
        # Cross-file test oracles, same rule: resolve before the marker runs.
        with timed(timings, "analysis.health.oracle_reach"):
            self._apply_cross_file_oracles(walked)
        # One shared dataflow service for the whole pass: the promotion pass
        # and the Extract Method detector read the same lazily parsed
        # per-file object, so no file is parsed twice for dataflow.
        dataflow_cache = FileDataflowCache(self.read_source)
        self.perf_stage.run(walked, dataflow_cache, timings)
        # Before the evaluation: untested_complexity reads each function's nearest test.
        with timed(timings, "analysis.health.function_tests"):
            function_tests = self.test_index.function_tests()
        with timed(timings, "analysis.health.repo_stats"):
            evaluator = self._file_evaluator(
                run, dup_report, fn_mod_p80, dataflow_cache, function_tests
            )
        with timed(timings, "analysis.health.evaluate"):
            metrics, findings, suggestions = self._evaluate_all(evaluator, walked, on_step)
        # KPIs are repo-wide; on an incremental run they would be biased
        # by the changed-files subset. Skip them in that case — the
        # ``persist`` step recomputes KPIs from the merged DB rows.
        kpis = self._kpis(metrics, findings) if changed_files is None else {}
        with timed(timings, "analysis.health.finalize"):
            execution_roles = self._mark_perf_entry_reachability(findings)
            suggestions = self._with_performance_plans(run, findings, suggestions)
        return HealthReport(
            repo_id="",
            analyzed_at=datetime.now(UTC),
            findings=findings,
            metrics=metrics,
            kpis=kpis,
            function_blame_rows=self._function_blame_rows(walked),
            execution_roles=execution_roles,
            function_facts=self._function_fact_rows(walked),
            function_tests=function_tests,
            repo_function_mod_p80=full_repo_fn_mod_p80,
            refactoring_suggestions=suggestions,
            performance_plan_policy=PerformancePlanPolicy(
                enabled=run.performance_fix,
                min_confidence=run.refactoring_min_confidence,
            ),
        )

    def _file_evaluator(
        self,
        run: _RunConfig,
        dup_report: DuplicationReport,
        fn_mod_p80: int | None,
        dataflow_cache: FileDataflowCache,
        function_tests: FunctionTests | None = None,
    ) -> FileEvaluator:
        """The repo-wide inputs every file's evaluation reads, built once per pass."""
        analyzed_paths = {pf.file_info.path for pf in self.parsed_files}
        return FileEvaluator(
            self,
            paired_tests=self.test_index.paired(analyzed_paths)
            | (self._tested_files & analyzed_paths),
            package_roots=self._package_boundaries(analyzed_paths),
            dup_report=dup_report,
            run=run,
            graph_view=ImportEdgeView(self.graph) if self.graph is not None else None,
            function_mod_p80=fn_mod_p80,
            dependents_p80=_compute_repo_dependents_p80(self.file_dependents()),
            active_contributors=_compute_repo_active_contributors(self.git_meta_map),
            file_scc_index=build_file_scc_index(self.graph),
            methods_by_file=build_methods_by_file(self.graph),
            split_partial_types=build_split_partial_types(self.graph),
            dataflow_cache=dataflow_cache,
            function_tests=function_tests,
            paired_test_files=paired_test_index(self.test_index.all_test_files()),
        )

    def _evaluate_all(
        self,
        evaluator: FileEvaluator,
        walked: list[tuple[Any, FileComplexity]],
        on_step: Any | None,
    ) -> tuple[list[HealthFileMetricData], list[HealthFindingData], list[RefactoringSuggestion]]:
        metrics: list[HealthFileMetricData] = []
        findings: list[HealthFindingData] = []
        suggestions: list[RefactoringSuggestion] = []
        for pf, fcx in walked:
            # Side effect: stamp each matched symbol's complexity_estimate.
            self._populate_symbol_complexity(pf, fcx.functions)
            file_metric, file_findings, file_suggestions = evaluator.evaluate(pf, fcx)
            # Every dataflow consumer has read this file by now.
            evaluator.dataflow_cache.release(pf.file_info.abs_path)
            metrics.append(file_metric)
            findings.extend(file_findings)
            suggestions.extend(file_suggestions)
            if on_step:
                on_step(pf.file_info.path)
        return metrics, findings, suggestions

    def _kpis(
        self, metrics: list[HealthFileMetricData], findings: list[HealthFindingData]
    ) -> dict[str, Any]:
        hotspot_paths = {p for p, meta in self.git_meta_map.items() if self._is_hotspot(meta)}
        return compute_kpis(metrics, hotspot_paths, deductions=deduction_by_path(findings))

    def _with_performance_plans(
        self,
        run: _RunConfig,
        findings: list[HealthFindingData],
        suggestions: list[RefactoringSuggestion],
    ) -> list[RefactoringSuggestion]:
        """Link the performance findings into opportunities, add their fix
        plans to *suggestions*, and rank the whole list."""
        link_performance_findings(findings)
        opportunities = build_performance_opportunities(findings)
        if run.performance_fix:
            suggestions.extend(
                performance_fix_suggestions(
                    opportunities,
                    min_confidence=run.refactoring_min_confidence,
                )
            )
        return rank_suggestions(suggestions, centrality=self._refactoring_centrality(suggestions))

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
            out[path] = float(self._dependents(path))
        return out

    def _apply_cross_file_oracles(self, walked: list[tuple[Any, FileComplexity]]) -> None:
        """Resolve each test's oracle across file boundaries, in place.

        A test that hands its checks to an asserting helper in another file is
        not assertion-free, and the per-function count cannot see that. This
        attaches the resolved lines so ``assertion_free_test`` stays a pure
        function over its context. Failure-isolated and a no-op without a
        graph, in which case the marker behaves exactly as it did before.
        """
        try:
            index = self._execution_graph()
            if self.graph is None or index is None:
                return
            by_file = collect_cross_file_oracles(walked, self.graph, index=index)
            for _pf, fcx in walked:
                resolved = by_file.get(_pf.file_info.path)
                if resolved:
                    fcx.cross_file_oracles = resolved
        except Exception as exc:
            log.debug("health_cross_file_oracles_failed", error=str(exc))
            return

    def _mark_perf_entry_reachability(
        self, findings: list[HealthFindingData]
    ) -> ExecutionRoles | None:
        """Stamp entry reachability and execution role without walking once per row.

        The role is keyed on the loop-owning function: the path's first node
        when the cost crosses functions, else the symbol holding the finding.
        The owner is stored too, so the writer can restamp a stored finding
        this run did not rescan. Returns the roles for that writer.
        """
        index = self._execution_graph()
        perf = [finding for finding in findings if finding.dimension == "performance"]
        if index is None or self.graph is None:
            return None
        entry_files = {
            node_id
            for node_id, data in self.graph.nodes(data=True)
            if data.get("node_type") == "file" and data.get("is_entry_point")
        }
        seeds = {symbol for path in entry_files for symbol in index.declares.get(path, ())}
        reachable = index.forward_reachable(seeds) if seeds and perf else None
        try:
            roles = ExecutionRoles.build(self.graph, index)
        except Exception as exc:
            log.debug("health_execution_roles_failed", error=str(exc))
            roles = None
        for finding in perf:
            path = finding.details.get("path")
            if reachable is not None and isinstance(path, list) and path:
                finding.details["reliable_entry_reachability"] = path[0] in reachable
            if roles is not None:
                owner = roles.owner_of(finding.file_path, finding.line_start, finding.details)
                finding.details["role_owner"] = owner
                finding.details["execution_role"] = roles.role_of(owner, finding.file_path)
        return roles

    def _function_fact_rows(
        self, walked: list[tuple[Any, FileComplexity]]
    ) -> list[dict] | None:
        """One row per walked function the graph names, for the ``function_facts`` store.

        A function the graph holds no symbol for has no id to key on, and is left out.
        Test files are left out too: no reader needs their functions yet (perf and
        Extract Method serve production code). Drop the filter when one does.
        """
        index = self._execution_graph()
        if index is None:
            return None
        rows: list[dict] = []
        for pf, fcx in walked:
            path = pf.file_info.path
            if pf.file_info.is_test:
                continue
            for fc in fcx.functions:
                symbol = index.resolve_function(path, fc.start_line, func_end=fc.end_line)
                if symbol is None:
                    continue
                facts = fc.facts
                rows.append(
                    {
                        "symbol_id": symbol,
                        "awaits": facts.awaits if facts else None,
                        "is_generator": facts.is_generator if facts else None,
                        "uses_receiver": facts.uses_receiver if facts else None,
                        "receiver_assigns": facts.receiver_assigns if facts else None,
                        "receiver_assigns_known": (
                            facts.receiver_assigns is not None if facts else None
                        ),
                        "early_exits": facts.early_exits if facts else None,
                        **fc.stored_metrics(),
                    }
                )
        return rows

    def _function_blame_rows(self, walked: list[tuple[Any, FileComplexity]]) -> list[dict]:
        """Build the per-function blame rollup from the walked files + the
        FULL-tier blame indexes attached to ``git_meta_map``.

        Cheap (reads the already-materialised blame index; no extra git) and
        failure-isolated so a rollup hiccup never breaks the health report.
        Returns an empty list on the ESSENTIAL tier (no blame indexes).
        """
        try:
            from .function_blame_rollup import build_function_blame_rows

            return build_function_blame_rows(list(walked), self.git_meta_map)
        except Exception as exc:
            log.debug("function_blame_rollup_failed", error=str(exc))
            return []

    def _walk(self, pf: Any, vocab: AssertVocabulary) -> FileComplexity:
        path = pf.file_info.abs_path
        language = pf.file_info.language
        source = self.read_source(path)
        if source is None:
            return FileComplexity(functions=[], classes=[])
        self._origins[pf.file_info.path] = self._origin(pf, source)
        key = None
        if self._walk_cache is not None:
            from repowise.core.ingestion import compute_content_hash
            from repowise.core.ingestion.parser import grammar_tag_for

            # The grammar, not the language: a .tsx file and a byte-identical
            # .ts file are walked by different grammars and must key apart.
            key = HealthWalkCache.key(
                grammar_tag_for(language, path), compute_content_hash(source), vocab.key
            )
            cached = self._walk_cache.get(key)
            if cached is not None:
                return cached
        if language == "sql":
            # SQL has no tree-sitter grammar here; the sqlglot-backed walker
            # produces routine CCN + the sql_* smell hits instead.
            from .sql_complexity import walk_sql_file

            fcx = walk_sql_file(pf.file_info, source)
        else:
            fcx = walk_file(path, language, source, vocab.names)
        if key is not None and self._walk_cache is not None:
            self._walk_cache.put(key, fcx)
        return fcx

    def _project(self) -> str | None:
        """The repository's name, so its own release banner is never read as a
        vendored library's. The ``origin`` remote names it even in a worktree;
        the checkout folder is the fallback. One git call per pass, on first use.
        """
        if self._project_name is None and self.repo_root is not None:
            root = str(self.repo_root)
            self._project_name = remote_name(root) or Path(root).name
        return self._project_name

    def _origin(self, pf: Any, source: bytes | None = None) -> CodeOrigin:
        return code_origin(
            pf.file_info.path,
            source,
            is_test=bool(pf.file_info.is_test),
            project=self._project(),
            repo_root=self.repo_root,
        )

    def _installed(self, path: str) -> bool:
        return self.repo_root is not None and is_installed_component(
            self.repo_root, path, self._installed_ui_dirs
        )

    def _without_kit_clones(
        self, file_path: str, clones: list[ClonePair], dup_pct: float | None
    ) -> tuple[list[ClonePair], float | None]:
        """*clones* less those both of whose sides are components a component
        CLI installed.

        Copies of one upstream kit (shadcn/ui's ``dialog.tsx`` and
        ``alert-dialog.tsx``, or the repeated item wrappers inside one of them)
        are similar by design, so such a clone is not duplication anyone here
        wrote. Every other finding on those files stands, and a clone with a
        file outside the kit still counts.
        The file's duplication share shrinks with the lines the dropped clones
        alone covered.
        """
        if not clones or not self._installed(file_path):
            return clones, dup_pct
        kept = [p for p in clones if not self._installed(_partner(file_path, p))]
        return kept, _pct_of_kept(file_path, clones, kept, dup_pct)

    def _load_walk_cache(self) -> None:
        if self._walk_cache is not None and self._owns_walk_cache:
            self._walk_cache.load()

    def _save_walk_cache(self) -> None:
        """Persist the walk entries this pass used or produced, if any."""
        if self._walk_cache is not None and self._owns_walk_cache:
            self._walk_cache.save()
            log.debug(
                "health_walk_cache",
                hits=self._walk_cache.hits,
                misses=self._walk_cache.misses,
            )

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

    def _read_repo_lines(self, path: str) -> list[str] | None:
        """*path*'s lines: the parsed file's own path, else under the repo root
        (an update's parsed set is the whole tree, but a caller may pass less)."""
        abs_path = self._abs_paths.get(path)
        if abs_path is None and self.repo_root is not None:
            abs_path = str(Path(self.repo_root) / path)
        return _read_source_lines(abs_path, self.read_source) if abs_path else None

    def _is_hotspot(self, meta: dict | object) -> bool:
        if isinstance(meta, dict):
            return bool(meta.get("is_hotspot", False))
        return bool(getattr(meta, "is_hotspot", False))
