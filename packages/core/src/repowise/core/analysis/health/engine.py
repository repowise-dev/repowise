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

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from .biomarkers import FileContext, detect_all
from .complexity import FunctionComplexity, walk_file_complexity
from .models import HealthFileMetricData, HealthFindingData, HealthReport
from .scoring import attach_impacts, compute_kpis, score_file

log = structlog.get_logger(__name__)


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
        or p.endswith(".spec.ts")
        or p.endswith(".spec.js")
        or p.endswith("_test.go")
    )


def _has_paired_test_file(rel_path: str, all_paths: set[str]) -> bool:
    """Heuristic: does any other file look like a test for *rel_path*?

    Cheap and conservative — looks for common test-file naming
    conventions paired with the same basename.
    """
    p = Path(rel_path)
    stem = p.stem
    candidates = {
        f"test_{stem}.py",
        f"{stem}_test.py",
        f"{stem}.test.ts",
        f"{stem}.test.tsx",
        f"{stem}.test.js",
        f"{stem}.spec.ts",
        f"{stem}.spec.js",
        f"{stem}_test.go",
    }
    return any(
        any(other.endswith("/" + c) or other == c for c in candidates) for other in all_paths
    )


class HealthAnalyzer:
    """Pure-Python health analyzer. No LLM, no network."""

    def __init__(
        self,
        graph: Any,  # networkx.DiGraph
        git_meta_map: dict[str, dict] | None = None,
        parsed_files: list[Any] | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}
        self.parsed_files = list(parsed_files or [])

    def analyze(
        self,
        config: dict | None = None,
        *,
        on_step: Any | None = None,
    ) -> HealthReport:
        cfg = config or {}
        disabled: list[str] = list(cfg.get("disabled_biomarkers", ()))

        # PageRank is optional — graph_builder.symbol_pagerank exists but
        # is symbol-level; we use file-level in-degree as the dependents
        # signal (cheap, deterministic, conservative).
        all_paths = {pf.file_info.path for pf in self.parsed_files}

        findings: list[HealthFindingData] = []
        metrics: list[HealthFileMetricData] = []

        for pf in self.parsed_files:
            try:
                fc_list = self._walk(pf)
            except Exception as exc:
                log.debug("health_walk_failed", path=pf.file_info.path, error=str(exc))
                fc_list = []

            # Side-effect: bump Symbol.complexity_estimate when we can
            # match by enclosing line range. Symbols not matched keep
            # their default (1).
            self._populate_symbol_complexity(pf, fc_list)

            file_metric, file_findings = self._evaluate_file(
                pf, fc_list, all_paths, disabled=disabled
            )
            metrics.append(file_metric)
            findings.extend(file_findings)

            if on_step:
                on_step(pf.file_info.path)

        hotspot_paths = {p for p, meta in self.git_meta_map.items() if self._is_hotspot(meta)}
        kpis = compute_kpis(metrics, hotspot_paths)

        return HealthReport(
            repo_id="",
            analyzed_at=datetime.now(UTC),
            findings=findings,
            metrics=metrics,
            kpis=kpis,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _walk(self, pf: Any) -> list[FunctionComplexity]:
        path = pf.file_info.abs_path
        language = pf.file_info.language
        try:
            source = Path(path).read_bytes()
        except OSError:
            return []
        return walk_file_complexity(path, language, source)

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
        fc_list: list[FunctionComplexity],
        all_paths: set[str],
        *,
        disabled: list[str],
    ) -> tuple[HealthFileMetricData, list[HealthFindingData]]:
        file_path = pf.file_info.path

        fn_metrics: dict[str, FunctionComplexity] = {fc.name: fc for fc in fc_list}
        max_ccn = max((fc.ccn for fc in fc_list), default=1)
        max_nesting = max((fc.max_nesting for fc in fc_list), default=0)
        nloc = sum(fc.nloc for fc in fc_list)

        dependents_count = 0
        if self.graph is not None and file_path in self.graph:
            try:
                dependents_count = int(self.graph.in_degree(file_path))
            except Exception:
                dependents_count = 0

        ctx = FileContext(
            file_path=file_path,
            language=pf.file_info.language,
            nloc=nloc,
            has_test_file=_has_paired_test_file(file_path, all_paths) or _is_test_file(file_path),
            module=None,
            function_metrics=fn_metrics,
            git_meta=self.git_meta_map.get(file_path, {}) or {},
            dependents_count=dependents_count,
            pagerank_score=0.0,
        )

        biomarker_results = detect_all(ctx, disabled=disabled)
        score, deductions = score_file(biomarker_results)
        findings = attach_impacts(biomarker_results, deductions)
        for f in findings:
            f.file_path = file_path

        metric = HealthFileMetricData(
            file_path=file_path,
            score=round(score, 2),
            max_ccn=max_ccn,
            max_nesting=max_nesting,
            nloc=nloc,
            has_test_file=ctx.has_test_file,
            module=None,
        )
        return metric, findings

    def _is_hotspot(self, meta: dict | object) -> bool:
        if isinstance(meta, dict):
            return bool(meta.get("is_hotspot", False))
        return bool(getattr(meta, "is_hotspot", False))
