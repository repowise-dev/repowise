"""Scores one walked file: biomarkers, findings, the file metric and its refactorings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...ingestion.git_indexer.function_blame import BlameIndex
from ...ingestion.package_roots import module_for as _module_for
from ..graph_view import HasEdge
from .asserts.lexicon import AssertVocabulary
from .biomarkers import FileContext, detect_all
from .biomarkers.base import BiomarkerResult
from .biomarkers.function_hotspot import refresh_replayed
from .complexity import FileComplexity, FunctionComplexity
from .complexity.languages import has_health_dialect
from .dataflow import FileDataflowCache
from .duplication import ClonePair, DuplicationReport
from .finding_identity import SYMBOL_INDEX_KEY, SYMBOL_KEY, SYMBOL_LINE_KEY
from .function_blame_rollup import blame_commit_entries, commit_spans
from .history_refresh import BLAME_MARKERS, as_biomarker_result
from .models import HealthFileMetricData, HealthFindingData, Severity
from .reexport_only import is_reexport_only
from .refactoring import RefactoringContext, RefactoringSuggestion, detect_refactorings
from .scoring import attach_impacts, file_score_fields, remap_severities, score_file
from .source_reader import SourceReader

if TYPE_CHECKING:
    from .engine import HealthAnalyzer

#: Function facts a finding inside the function carries in its details.
_FUNCTION_FACTS = ("deprecated", "gated_off")


def _mark_function_facts(
    findings: list[HealthFindingData], functions: list[FunctionComplexity]
) -> None:
    """Stamp ``deprecated`` / ``gated_off`` on findings inside such a function; absent elsewhere."""
    spans = [
        (fact, fc.name, fc.start_line, fc.end_line)
        for fc in functions
        for fact in _FUNCTION_FACTS
        if getattr(fc, fact, False)
    ]
    for finding in findings:
        if finding.function_name is None:
            continue
        line = finding.line_start
        for fact, name, start, end in spans:
            if name == finding.function_name and (line is None or start <= line <= end):
                finding.details[fact] = True


#: Function-size findings whose fix starts at the deepest nested block.
_DEEPEST_BLOCK_MARKERS = frozenset(
    {"complex_method", "nested_complexity", "brain_method", "large_method", "repeated_nesting"}
)


def _mark_deepest_block(
    findings: list[HealthFindingData], functions: list[FunctionComplexity]
) -> None:
    """Copy the function's deepest nested block onto its size findings as ``deepest_block``."""
    blocks = {
        (fc.name, fc.start_line): fc.deepest_block
        for fc in functions
        if fc.deepest_block
    }
    if not blocks:
        return
    for finding in findings:
        if finding.biomarker_type not in _DEEPEST_BLOCK_MARKERS:
            continue
        block = blocks.get((finding.function_name, finding.line_start))
        if block:
            finding.details["deepest_block"] = {"start": block[0], "end": block[1]}


def walked_functions(
    fc_list: list[FunctionComplexity], language: str
) -> tuple[FunctionComplexity, ...]:
    """The functions a file's biomarkers see, in document order (stable sort).

    SQL is held back: its text-counted metrics are not calibrated for the method markers.
    """
    if language == "sql":
        return ()
    return tuple(sorted(fc_list, key=lambda fc: (fc.start_line, fc.end_line)))


def _stamp_symbol_lines(findings: list[HealthFindingData], fcx: FileComplexity) -> None:
    """Anchor each finding on its innermost enclosing symbol, so edits above it keep the id.

    ``symbol_index`` separates same-named symbols; a replayed finding keeps its anchor.
    """
    spans = sorted(
        [(fc.start_line, fc.end_line, fc.name) for fc in fcx.functions]
        + [(c.start_line, c.end_line, c.name) for c in fcx.classes]
    )
    for f in findings:
        if f.line_start is None or SYMBOL_LINE_KEY in f.details:
            continue
        holding = [
            span
            for span in spans
            if span[0] <= f.line_start <= span[1]
            and (not f.function_name or span[2] == f.function_name)
        ]
        if not holding:
            continue
        start, _, name = max(holding, key=lambda span: (span[0], -span[1]))
        stamp: dict = {SYMBOL_LINE_KEY: start}
        if not f.function_name:
            stamp[SYMBOL_KEY] = name
        index = sum(1 for span in spans if span[2] == name and span[0] < start)
        if index:
            stamp[SYMBOL_INDEX_KEY] = index
        f.details = {**f.details, **stamp}


#: Method-level smells that earn a file the dataflow pass for Extract Method.
_EXTRACT_METHOD_SOURCES = frozenset({"large_method", "brain_method", "complex_method"})


def _commit_entries(fcx: FileComplexity, git_meta: dict) -> list:
    """Per-function commit sets: from the blame index, else the stored rows."""
    idx = git_meta.get("blame_index") or git_meta.get("commit_set_blame")
    if isinstance(idx, BlameIndex):
        return blame_commit_entries(fcx.functions, idx)
    return list(git_meta.get("function_commit_shas") or ())


def _read_source_lines(abs_path: str, read_source: SourceReader) -> list[str] | None:
    """A file's lines for display, or ``None`` on any read or decode failure."""
    raw = read_source(abs_path)
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8", errors="replace")
    except (UnicodeError, AttributeError):
        return None
    return text.splitlines()


def _clone_sources(
    file_path: str,
    own_lines: list[str],
    clones: list[ClonePair],
    read_lines: Callable[[str], list[str] | None],
) -> dict[str, list[str]]:
    """This file's lines plus each clone partner's, for text-level checks."""
    out = {file_path: own_lines}
    for clone in clones:
        for path in (clone.file_a, clone.file_b):
            if path in out:
                continue
            lines = read_lines(path)
            if lines is not None:
                out[path] = lines
    return out


@dataclass(frozen=True, slots=True)
class _RunConfig:
    """The analyzer config one pass reads, with its per-file overrides."""

    vocab: AssertVocabulary
    disabled: list[str]
    per_file_disabled: dict[str, set[str]]
    severity_overrides: dict[str, Severity]
    per_file_severity_overrides: dict[str, dict[str, Severity]]
    disabled_refactorings: list[str]
    refactoring_enabled: bool
    refactoring_min_confidence: str | None

    @classmethod
    def read(cls, config: dict | None) -> _RunConfig:
        cfg = config or {}
        return cls(
            vocab=AssertVocabulary.from_analyzer_config(cfg),
            disabled=list(cfg.get("disabled_biomarkers", ())),
            per_file_disabled=cfg.get("per_file_disabled", {}) or {},
            severity_overrides=cfg.get("severity_overrides", {}) or {},
            per_file_severity_overrides=cfg.get("per_file_severity_overrides", {}) or {},
            disabled_refactorings=list(cfg.get("disabled_refactorings", ())),
            refactoring_enabled=bool(cfg.get("refactoring_enabled", True)),
            refactoring_min_confidence=cfg.get("refactoring_min_confidence"),
        )

    @property
    def performance_fix(self) -> bool:
        return self.refactoring_enabled and "performance_fix" not in self.disabled_refactorings

    def file_disabled(self, path: str) -> list[str]:
        out = list(self.disabled)
        for name in self.per_file_disabled.get(path) or ():
            if name not in out:
                out.append(name)
        return out

    def file_severity_overrides(self, path: str) -> dict[str, Severity] | None:
        out = dict(self.severity_overrides)
        out.update(self.per_file_severity_overrides.get(path, {}))
        return out or None


@dataclass(frozen=True, slots=True)
class FileEvaluator:
    """Evaluates each walked file against one pass's repo-wide inputs; later fields are optional."""

    analyzer: HealthAnalyzer
    paired_tests: set[str]
    package_roots: set[str]
    dup_report: DuplicationReport
    run: _RunConfig = field(default_factory=lambda: _RunConfig.read(None))
    graph_view: HasEdge | None = None
    function_mod_p80: int | None = None
    dependents_p80: int | None = None
    active_contributors: int | None = None
    file_scc_index: dict[str, tuple[str, ...]] | None = None
    methods_by_file: dict[str, tuple[str, ...]] | None = None
    dataflow_cache: FileDataflowCache | None = None

    def evaluate(
        self, pf: Any, fcx: FileComplexity
    ) -> tuple[HealthFileMetricData, list[HealthFindingData], list[RefactoringSuggestion]]:
        file_path = pf.file_info.path
        clones, dup_pct = self.analyzer._without_kit_clones(
            file_path,
            self.dup_report.pairs_by_file.get(file_path, []),
            self.dup_report.duplication_pct.get(file_path),
        )
        source_lines = (
            _read_source_lines(pf.file_info.abs_path, self.analyzer.read_source)
            if clones
            else None
        )
        ctx = self._context(pf, fcx, clones, dup_pct, source_lines)
        findings, scores = self._findings(ctx, fcx)
        metric = self._metric(pf, fcx, ctx, scores, findings)

        if not self.run.refactoring_enabled:
            return metric, findings, []
        return metric, findings, self._suggestions(pf, fcx, ctx, findings, source_lines)

    def _context(
        self,
        pf: Any,
        fcx: FileComplexity,
        clones: list[ClonePair],
        dup_pct: float | None,
        source_lines: list[str] | None,
    ) -> FileContext:
        """The biomarkers' view of the file."""
        analyzer = self.analyzer
        file_path = pf.file_info.path
        cov = analyzer.coverage_map.get(file_path)
        if cov is None:
            cov = analyzer.coverage_map.get(file_path.replace("\\", "/"))
        clone_sources = (
            _clone_sources(file_path, source_lines, clones, analyzer._repo_lines)
            if source_lines is not None and pf.file_info.is_test
            else {}
        )
        file_git_meta = analyzer.git_meta_map.get(file_path, {}) or {}
        blame_idx_obj = file_git_meta.get("blame_index")
        return FileContext(
            file_path=file_path,
            language=pf.file_info.language,
            nloc=fcx.file_nloc,
            # Ingestion's own test decision, not one re-derived from the path.
            has_test_file=file_path in self.paired_tests
            or pf.file_info.is_test
            or fcx.has_inline_tests,
            is_test=bool(pf.file_info.is_test),
            # Separate from has_test_file (import or name pairing) so each signal stays visible.
            reached_by_tests=file_path in analyzer.test_index.reached_files(),
            module=_module_for(file_path, self.package_roots),
            all_functions=walked_functions(fcx.functions, pf.file_info.language),
            class_metrics=fcx.classes,
            git_meta=file_git_meta,
            dependents_count=analyzer._dependents(file_path),
            repo_dependents_p80=self.dependents_p80,
            pagerank_score=0.0,
            line_coverage_pct=cov.get("line_coverage_pct") if cov else None,
            branch_coverage_pct=cov.get("branch_coverage_pct") if cov else None,
            covered_lines=set(cov.get("covered_lines") or ()) if cov else set(),
            total_coverable_lines=int(cov.get("total_coverable_lines", 0)) if cov else 0,
            reexport_only=bool(cov)
            and is_reexport_only(pf, bool(fcx.functions), analyzer.read_source),
            clones=list(clones),
            duplication_pct=dup_pct,
            clone_sources=clone_sources,
            graph_view=self.graph_view,
            blame_index=blame_idx_obj if isinstance(blame_idx_obj, BlameIndex) else None,
            repo_function_mod_p80=self.function_mod_p80,
            repo_active_contributors_90d=self.active_contributors,
            error_handling_hits=fcx.error_handling_hits,
            perf_hits=fcx.perf_hits,
            io_boundary_names=set(fcx.io_boundary_names),
            cross_file_oracle_lines=frozenset(getattr(fcx, "cross_file_oracles", ())),
        )

    def _findings(
        self, ctx: FileContext, fcx: FileComplexity
    ) -> tuple[list[HealthFindingData], dict[str, float | None]]:
        """Run the biomarkers, score the file and stamp each finding's anchors."""
        file_path = ctx.file_path
        disabled = self.run.file_disabled(file_path)
        biomarker_results = detect_all(ctx, disabled=disabled)
        if ctx.blame_index is None:
            biomarker_results.extend(self._replayed(ctx, disabled))
        biomarker_results = remap_severities(
            biomarker_results, self.run.file_severity_overrides(file_path)
        )
        scores, deductions = score_file(biomarker_results)
        findings = attach_impacts(biomarker_results, deductions)
        for f in findings:
            f.file_path = file_path
        _mark_function_facts(findings, fcx.functions)
        _stamp_symbol_lines(findings, fcx)
        _mark_deepest_block(findings, fcx.functions)
        return findings, scores

    def _replayed(self, ctx: FileContext, disabled: list[str]) -> list[BiomarkerResult]:
        """Stored blame-marker findings this pass cannot recompute without a blame index."""
        out = []
        for stored in self.analyzer.stored_blame_findings.get(ctx.file_path, ()):
            replayed = as_biomarker_result(stored)
            if replayed.biomarker_type not in BLAME_MARKERS or replayed.biomarker_type in disabled:
                continue
            if replayed.biomarker_type == "function_hotspot" and not refresh_replayed(
                replayed, ctx.all_functions
            ):
                continue
            out.append(replayed)
        return out

    def _metric(
        self,
        pf: Any,
        fcx: FileComplexity,
        ctx: FileContext,
        scores: dict[str, float | None],
        findings: list[HealthFindingData],
    ) -> HealthFileMetricData:
        # A language with no dialect stores no scores and no complexity figures.
        analysed = has_health_dialect(pf.file_info.language)
        fc_list = fcx.functions
        return HealthFileMetricData(
            file_path=ctx.file_path,
            max_ccn=max((fc.ccn for fc in fc_list), default=1) if analysed else None,
            max_nesting=max((fc.max_nesting for fc in fc_list), default=0) if analysed else None,
            nloc=fcx.file_nloc,
            # "Does something test this file": the union of pairing and graph reach.
            has_test_file=ctx.has_test_file or ctx.reached_by_tests,
            module=ctx.module,
            line_coverage_pct=ctx.line_coverage_pct,
            branch_coverage_pct=ctx.branch_coverage_pct,
            duplication_pct=ctx.duplication_pct,
            **file_score_fields(analysed, scores, findings),
            is_test=bool(pf.file_info.is_test),
            code_origin=self.analyzer._origins.get(ctx.file_path) or self.analyzer._origin(pf),
        )

    def _suggestions(
        self,
        pf: Any,
        fcx: FileComplexity,
        ctx: FileContext,
        findings: list[HealthFindingData],
        source_lines: list[str] | None,
    ) -> list[RefactoringSuggestion]:
        file_path = ctx.file_path
        analyzer = self.analyzer
        rctx = RefactoringContext(
            file_path=file_path,
            language=pf.file_info.language,
            nloc=fcx.file_nloc,
            classes=fcx.classes,
            findings=findings,
            dependents_count=ctx.dependents_count,
            clones=list(ctx.clones),
            community_label_map=analyzer.community_label_map,
            graph=analyzer.graph,
            file_scc=(self.file_scc_index or {}).get(file_path),
            file_methods=(
                self.methods_by_file.get(file_path, ())
                if self.methods_by_file is not None
                else None
            ),
            function_analyses=self._extract_method_analyses(pf, findings),
            commit_spans=commit_spans(fcx.functions, _commit_entries(fcx, ctx.git_meta)),
            source_lines=source_lines,
            read_lines=analyzer._repo_lines,
        )
        return detect_refactorings(
            rctx,
            disabled=self.run.disabled_refactorings or (),
            min_confidence=self.run.refactoring_min_confidence,
        )

    def _extract_method_analyses(self, pf: Any, findings: list[HealthFindingData]) -> list[Any]:
        """Dataflow analyses for Extract Method, built only for files with a method-level smell."""
        if not any(getattr(f, "biomarker_type", "") in _EXTRACT_METHOD_SOURCES for f in findings):
            return []
        cache = (
            self.dataflow_cache
            if self.dataflow_cache is not None
            else FileDataflowCache(self.analyzer.read_source)
        )
        return cache.get(pf.file_info.abs_path, pf.file_info.language).flagged_analyses()
