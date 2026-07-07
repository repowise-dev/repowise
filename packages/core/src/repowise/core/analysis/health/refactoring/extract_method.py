"""Extract Method detector -- the first dataflow-driven refactoring.

When a function is flagged ``large_method`` / ``brain_method`` /
``complex_method``, the dataflow layer (CFG + def/use + reaching definitions)
finds a contiguous statement span that can be lifted into a helper without
changing behaviour, and infers that helper's signature (IN parameters,
OUT return). This detector turns the best such span into one structured
``RefactoringSuggestion`` per flagged function.

The candidate spans + IN/OUT come from ``dataflow.find_extractions``; this
module only matches each analysed function to the biomarker finding that flags
it (for the recovered impact), picks the strongest extraction, and renders the
plan. Precision-first: a function with no safe, complexity-removing span yields
no suggestion.

Plan shape (open dict, no migration):

- ``plan`` = ``{"span": {"start": int, "end": int}, "params": [str, ...],
  "returns": [str, ...], "suggested_name": str | None}`` -- the lines to lift,
  the inferred signature, and an optional name (left ``None`` for the codegen /
  LLM step to choose).
- ``evidence`` = ``{"slice_nloc": int, "ccn_removed": int}`` -- the size and
  complexity the residual method sheds.
- ``blast_radius`` = ``{"callers_count": int}`` -- extraction is local (a new
  private helper, the public method's signature is unchanged), so callers do
  not change; carried for the surfaces' consistency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..complexity.languages import get_language_map
from ..dataflow import find_extractions
from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, effort_bucket, register

if TYPE_CHECKING:
    from ..dataflow import Extraction, FunctionAnalysis

# The function-level structural biomarkers this detector answers. A function is
# only offered an extraction when one of these flagged it, so the suggestion
# list never exceeds (and stays consistent with) what health surfaces.
_SOURCE_BIOMARKERS = ("brain_method", "large_method", "complex_method")


@register
class ExtractMethodDetector(RefactoringDetector):
    name = "extract_method"

    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        analyses: list[FunctionAnalysis] = list(getattr(ctx, "function_analyses", []) or [])
        if not analyses:
            return []
        lmap = get_language_map(ctx.language)
        if lmap is None:
            return []

        out: list[RefactoringSuggestion] = []
        for analysis in analyses:
            impact, source = self._impact_for(analysis, ctx.findings)
            if not source:
                # Only suggest where a method biomarker actually fired.
                continue
            candidates = find_extractions(analysis, lmap)
            if not candidates:
                continue
            best = candidates[0]  # already best-first
            out.append(
                RefactoringSuggestion(
                    refactoring_type=self.name,
                    file_path=ctx.file_path,
                    target_symbol=analysis.name,
                    line_start=analysis.start_line,
                    line_end=analysis.end_line,
                    plan={
                        "span": {"start": best.start_line, "end": best.end_line},
                        "params": list(best.params),
                        "returns": list(best.returns),
                        "suggested_name": None,
                    },
                    evidence={
                        "slice_nloc": best.slice_nloc,
                        "ccn_removed": best.ccn_removed,
                    },
                    impact_delta=round(float(impact), 3),
                    effort_bucket=effort_bucket(best.slice_nloc),
                    blast_radius={"callers_count": 0},
                    confidence=self._confidence(best),
                    source_biomarker=source,
                )
            )

        # Stable order: biggest recovery first, then symbol, then span start.
        out.sort(key=lambda s: (-s.impact_delta, s.target_symbol, s.line_start or 0))
        return out

    @staticmethod
    def _impact_for(analysis: FunctionAnalysis, findings: list[Any]) -> tuple[float, str]:
        """Recovered impact + source biomarker for *analysis*, from the file's
        method-smell findings. Matches by function name and line containment so
        the right finding is picked when a name repeats."""
        best_impact = 0.0
        best_source = ""
        for f in findings:
            if getattr(f, "biomarker_type", "") not in _SOURCE_BIOMARKERS:
                continue
            if getattr(f, "function_name", "") != analysis.name:
                continue
            line = getattr(f, "line_start", None)
            if line is not None and not (analysis.start_line <= line <= analysis.end_line):
                continue
            impact = float(getattr(f, "health_impact", 0.0) or 0.0)
            if impact >= best_impact:
                best_impact = impact
                best_source = getattr(f, "biomarker_type", "")
        return best_impact, best_source

    @staticmethod
    def _confidence(extraction: Extraction) -> str:
        """High when the extraction is unambiguous -- it removes several decision
        points with a clean signature; medium otherwise. (Every emitted span is
        single-exit with at most one return by construction.)"""
        if extraction.ccn_removed >= 2 and len(extraction.params) <= 4:
            return "high"
        return "medium"
