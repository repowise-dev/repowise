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
from .biomarkers import FileContext, detect_all
from .biomarkers.base import HasEdge
from .complexity import FileComplexity, FunctionComplexity, walk_file
from .coverage import is_test_file as _coverage_is_test_file
from .duplication import DuplicationReport, detect_clones
from .models import HealthFileMetricData, HealthFindingData, HealthReport
from .perf import (
    CallGraphIndex,
    PerfRanker,
    collect_blocking_io_under_lock,
    collect_centrality_gated,
    collect_crossfn_io_in_loop,
)
from .scoring import attach_impacts, compute_kpis, score_file

log = structlog.get_logger(__name__)


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


def _is_test_file(rel_path: str) -> bool:
    p = rel_path.lower()
    return (
        "/test/" in p
        or "/tests/" in p
        or "/__tests__/" in p
        or p.startswith("test_")
        or p.endswith("_test.py")
        or p.endswith(".test.ts")
        or p.endswith(".test.tsx")
        or p.endswith(".test.js")
        or p.endswith(".test.mts")
        or p.endswith(".test.cts")
        or p.endswith(".spec.ts")
        or p.endswith(".spec.js")
        or p.endswith(".spec.mts")
        or p.endswith(".spec.cts")
        or p.endswith("_test.go")
    )


def _fallback_module(rel_path: str) -> str | None:
    """Top-level directory as a stand-in module label when no community map.

    Returns ``None`` for root-level files so the rollup endpoint doesn't
    create a phantom "" bucket.
    """
    norm = rel_path.replace("\\", "/")
    if "/" not in norm:
        return None
    head = norm.split("/", 1)[0]
    return head or None


class _ImportEdgeView:
    """Thin ``HasEdge`` adapter over a NetworkX DiGraph.

    The graph stores ``edge_type`` as an attribute on each (single)
    edge between two file nodes. We look it up directly rather than
    pulling NetworkX into the biomarker test surface.
    """

    __slots__ = ("_graph",)

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    def has_edge(self, src: str, dst: str, key: str = "imports") -> bool:
        g = self._graph
        if g is None:
            return False
        try:
            if not g.has_edge(src, dst):
                return False
            data = g.get_edge_data(src, dst) or {}
        except Exception:
            return False
        return data.get("edge_type") == key


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


def _build_repo_commit_counts(git_meta_map: dict[str, dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for path, meta in git_meta_map.items():
        if not isinstance(meta, dict):
            continue
        try:
            out[path] = int(meta.get("commit_count_total") or 0)
        except (TypeError, ValueError):
            continue
    return out


def _path_basenames(all_paths: set[str]) -> set[str]:
    """Final path components of *all_paths*, split on ``/`` only.

    A path matches ``other.endswith("/" + c) or other == c`` for a
    slash-free candidate filename ``c`` exactly when its ``/``-basename
    equals ``c``, so one precomputed basename set answers every
    ``_has_paired_test_file`` lookup. Splitting on ``/`` only (not ``\\``)
    preserves that equivalence for any non-POSIX path that slips in.
    """
    return {p.rsplit("/", 1)[-1] for p in all_paths}


def _has_paired_test_file(rel_path: str, path_basenames: set[str]) -> bool:
    """Heuristic: does any other file look like a test for *rel_path*?

    Cheap and conservative — looks for common test-file naming
    conventions paired with the same basename. *path_basenames* is the
    precomputed ``_path_basenames`` set for the analyzed file list.
    """
    p = Path(rel_path)
    stem = p.stem
    candidates = {
        f"test_{stem}.py",
        f"{stem}_test.py",
        f"{stem}.test.ts",
        f"{stem}.test.tsx",
        f"{stem}.test.js",
        f"{stem}.test.mts",
        f"{stem}.test.cts",
        f"{stem}.spec.ts",
        f"{stem}.spec.js",
        f"{stem}.spec.mts",
        f"{stem}.spec.cts",
        f"{stem}_test.go",
    }
    return not candidates.isdisjoint(path_basenames)


class HealthAnalyzer:
    """Pure-Python health analyzer. No LLM, no network."""

    def __init__(
        self,
        graph: Any,  # networkx.DiGraph
        git_meta_map: dict[str, dict] | None = None,
        parsed_files: list[Any] | None = None,
        coverage_map: dict[str, dict[str, Any]] | None = None,
        module_map: dict[str, str] | None = None,
        duplication_cache_dir: Any | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}
        self.parsed_files = list(parsed_files or [])
        # Per-file coverage keyed by repo-relative POSIX path. Each value
        # is ``{line_coverage_pct, branch_coverage_pct, covered_lines,
        # total_coverable_lines}``. ``None``-equivalent files are simply
        # absent from the map.
        self.coverage_map = coverage_map or {}
        # Per-file module label keyed by repo-relative POSIX path.
        # Populated from graph community labels by the orchestrator. When
        # missing, the engine falls back to the top-level directory so
        # module rollups still group sensibly on small repos that didn't
        # produce community labels.
        self.module_map = module_map or {}
        # Directory for the duplication token/window cache (typically the
        # repo's ``.repowise``). None disables caching — the duplication
        # pass then re-tokenizes everything, exactly as before.
        self.duplication_cache_dir = duplication_cache_dir

    def analyze(
        self,
        config: dict | None = None,
        *,
        on_step: Any | None = None,
        changed_files: set[str] | list[str] | None = None,
    ) -> HealthReport:
        """Analyze the configured parsed files.

        Pass *changed_files* (repo-relative POSIX paths) for incremental
        runs from ``repowise update`` — the engine still needs the full
        parsed-file set to build duplication context (clones cross
        files), but only files in *changed_files* contribute findings /
        metrics. The caller is responsible for upserting (not replacing)
        the result against the existing rows.
        """
        cfg = config or {}
        disabled: list[str] = list(cfg.get("disabled_biomarkers", ()))
        per_file_disabled: dict[str, set[str]] = cfg.get("per_file_disabled", {}) or {}
        changed_set: set[str] | None = set(changed_files) if changed_files is not None else None

        # PageRank is optional — graph_builder.symbol_pagerank exists but
        # is symbol-level; we use file-level in-degree as the dependents
        # signal (cheap, deterministic, conservative).
        path_basenames = _path_basenames({pf.file_info.path for pf in self.parsed_files})
        repo_commit_counts = _build_repo_commit_counts(self.git_meta_map)
        graph_view: HasEdge | None = _ImportEdgeView(self.graph) if self.graph is not None else None

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
                    changed_files=changed_set,
                )
                _log_duplication_diagnostics(dup_report)
            except Exception as exc:
                log.debug("health_duplication_failed", error=str(exc))
                dup_report = DuplicationReport()

        findings: list[HealthFindingData] = []
        metrics: list[HealthFileMetricData] = []

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

        repo_fn_mod_p80 = _compute_repo_function_mod_p80(walked, self.git_meta_map)
        repo_dependents_p80 = _compute_repo_dependents_p80(self.parsed_files, self.graph)
        repo_active_contributors = _compute_repo_active_contributors(self.git_meta_map)

        # Cross-function N+1: augment perf_hits before the biomarker stage.
        self._apply_crossfn_perf(walked)

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
            file_metric, file_findings = self._evaluate_file(
                pf,
                fcx,
                path_basenames,
                disabled=file_disabled,
                dup_report=dup_report,
                graph_view=graph_view,
                repo_commit_counts=repo_commit_counts,
                repo_function_mod_p80=repo_fn_mod_p80,
                repo_dependents_p80=repo_dependents_p80,
                repo_active_contributors_90d=repo_active_contributors,
            )
            metrics.append(file_metric)
            findings.extend(file_findings)

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

        return HealthReport(
            repo_id="",
            analyzed_at=datetime.now(UTC),
            findings=findings,
            metrics=metrics,
            kpis=kpis,
            function_blame_rows=self._function_blame_rows(walked),
        )

    async def analyze_async(
        self,
        config: dict | None = None,
        *,
        on_step: Any | None = None,
        changed_files: set[str] | list[str] | None = None,
        max_workers: int | None = None,
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
        """
        cfg = config or {}
        disabled: list[str] = list(cfg.get("disabled_biomarkers", ()))
        per_file_disabled: dict[str, set[str]] = cfg.get("per_file_disabled", {}) or {}
        changed_set: set[str] | None = set(changed_files) if changed_files is not None else None

        path_basenames = _path_basenames({pf.file_info.path for pf in self.parsed_files})
        repo_commit_counts = _build_repo_commit_counts(self.git_meta_map)
        graph_view: HasEdge | None = _ImportEdgeView(self.graph) if self.graph is not None else None

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
        repo_fn_mod_p80 = _compute_repo_function_mod_p80(list(walked), self.git_meta_map)
        repo_dependents_p80 = _compute_repo_dependents_p80(self.parsed_files, self.graph)
        repo_active_contributors = _compute_repo_active_contributors(self.git_meta_map)

        # Cross-function N+1: augment perf_hits before the biomarker stage.
        walked = list(walked)
        self._apply_crossfn_perf(walked)

        findings: list[HealthFindingData] = []
        metrics: list[HealthFileMetricData] = []
        for pf, fcx in walked:
            self._populate_symbol_complexity(pf, fcx.functions)
            file_disabled = list(disabled)
            extra = per_file_disabled.get(pf.file_info.path)
            if extra:
                for name in extra:
                    if name not in file_disabled:
                        file_disabled.append(name)
            file_metric, file_findings = self._evaluate_file(
                pf,
                fcx,
                path_basenames,
                disabled=file_disabled,
                dup_report=dup_report,
                graph_view=graph_view,
                repo_commit_counts=repo_commit_counts,
                repo_function_mod_p80=repo_fn_mod_p80,
                repo_dependents_p80=repo_dependents_p80,
                repo_active_contributors_90d=repo_active_contributors,
            )
            metrics.append(file_metric)
            findings.extend(file_findings)
            if on_step:
                on_step(pf.file_info.path)

        if changed_set is None:
            hotspot_paths = {p for p, meta in self.git_meta_map.items() if self._is_hotspot(meta)}
            kpis = compute_kpis(metrics, hotspot_paths)
        else:
            kpis = {}

        return HealthReport(
            repo_id="",
            analyzed_at=datetime.now(UTC),
            findings=findings,
            metrics=metrics,
            kpis=kpis,
            function_blame_rows=self._function_blame_rows(walked),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _apply_crossfn_perf(self, walked: list[tuple[Any, FileComplexity]]) -> None:
        """Run the graph-dependent perf passes over the walked files, in place.

        Four sources of extra ``perf_hits``, all sharing one
        :class:`CallGraphIndex` (built once over the resolved ``calls`` graph):

          1. cross-function ``io_in_loop`` / N+1 (PR4);
          2. cross-function ``blocking_io_under_lock`` (Phase 7b) — the lock→I/O
             reachability case;
          3. the centrality-gated ``nested_loop_quadratic`` / ``hot_path_sync_io``
             markers (Phase 7b), generated from the walker's per-function facts
             ONLY for a hot function, via the :class:`PerfRanker`.

        Each is appended onto the matching file's ``perf_hits`` in place so the
        biomarkers handle every case through one path. Failure-isolated and never
        blocks the report. The cross-function passes are a no-op without a graph;
        the centrality-gated pass ALWAYS runs — when no graph/git signal is
        available nothing is hot, so it emits nothing (precision-first: we never
        ship a centrality-gated marker we cannot establish centrality for).
        """
        try:
            index = CallGraphIndex(self.graph) if self.graph is not None else None
            by_file: dict[str, list] = {}
            if self.graph is not None and index is not None:
                for src in (
                    collect_crossfn_io_in_loop(walked, self.graph, index=index),
                    collect_blocking_io_under_lock(walked, self.graph, index=index),
                ):
                    for path, hits in src.items():
                        by_file.setdefault(path, []).extend(hits)
            ranker = PerfRanker(index, self.git_meta_map)
            for path, hits in collect_centrality_gated(walked, ranker).items():
                by_file.setdefault(path, []).extend(hits)
            for _pf, fcx in walked:
                extra = by_file.get(_pf.file_info.path)
                if extra:
                    fcx.perf_hits = [*fcx.perf_hits, *extra]
        except Exception as exc:
            log.debug("health_crossfn_perf_failed", error=str(exc))
            return

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
        try:
            source = Path(path).read_bytes()
        except OSError:
            return FileComplexity(functions=[], classes=[])
        return walk_file(path, language, source)

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
        *,
        disabled: list[str],
        dup_report: DuplicationReport,
        graph_view: HasEdge | None = None,
        repo_commit_counts: dict[str, int] | None = None,
        repo_function_mod_p80: int | None = None,
        repo_dependents_p80: int | None = None,
        repo_active_contributors_90d: int | None = None,
    ) -> tuple[HealthFileMetricData, list[HealthFindingData]]:
        file_path = pf.file_info.path

        fc_list = fcx.functions
        fn_metrics: dict[str, FunctionComplexity] = {fc.name: fc for fc in fc_list}
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

        module = self.module_map.get(file_path) or _fallback_module(file_path)

        file_git_meta = self.git_meta_map.get(file_path, {}) or {}
        blame_idx_obj = file_git_meta.get("blame_index")
        blame_index = blame_idx_obj if isinstance(blame_idx_obj, BlameIndex) else None

        ctx = FileContext(
            file_path=file_path,
            language=pf.file_info.language,
            nloc=nloc,
            has_test_file=_has_paired_test_file(file_path, path_basenames)
            or _is_test_file(file_path)
            or _coverage_is_test_file(file_path),
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
            repo_commit_counts=repo_commit_counts or {},
            blame_index=blame_index,
            repo_function_mod_p80=repo_function_mod_p80,
            repo_active_contributors_90d=repo_active_contributors_90d,
            error_handling_hits=fcx.error_handling_hits,
            perf_hits=fcx.perf_hits,
            io_boundary_names=set(fcx.io_boundary_names),
        )

        biomarker_results = detect_all(ctx, disabled=disabled)
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
            has_test_file=ctx.has_test_file,
            module=module,
            line_coverage_pct=line_cov,
            branch_coverage_pct=branch_cov,
            duplication_pct=dup_pct,
            defect_score=round(defect_score, 2),
            maintainability_score=(round(maint_score, 2) if maint_score is not None else None),
            performance_score=(round(perf_score, 2) if perf_score is not None else None),
        )
        return metric, findings

    def _is_hotspot(self, meta: dict | object) -> bool:
        if isinstance(meta, dict):
            return bool(meta.get("is_hotspot", False))
        return bool(getattr(meta, "is_hotspot", False))
