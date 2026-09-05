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
  the inferred signature, and a deterministic starting name (see
  ``_suggested_name``). ``None`` when the span has no single informative OUT
  value: a name derived from the enclosing function described the context
  rather than the span, and collided with every sibling plan in the file.
- ``evidence`` = ``{"slice_nloc": int, "ccn_removed": int}`` -- the size and
  complexity the residual method sheds.
- ``blast_radius`` = ``{"scope": "local"}`` -- extraction is local (a new
  private helper, the public method's signature is unchanged), so nothing
  outside the file moves. This is a *categorical* statement, not a count: it
  replaced ``{"callers_count": 0}``, a hardcoded literal that no consumer could
  tell apart from a measured zero. Every other detector's blast radius is
  measured, so this one says in its own vocabulary that there is nothing to
  measure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..complexity.languages import get_language_map
from ..dataflow import find_extractions
from .models import RefactoringContext, RefactoringSuggestion
from .naming import identifier_slug
from .registry import RefactoringDetector, effort_bucket, register

if TYPE_CHECKING:
    from ..dataflow import Extraction, FunctionAnalysis

# OUT values whose name describes the variable's role, not the block's
# product: ``compute_result`` names nothing the reader did not know.
_UNINFORMATIVE_OUT = frozenset(
    {"out", "result", "results", "value", "values", "ret", "tmp", "temp", "data", "item"}
)

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
                        "suggested_name": self._suggested_name(analysis, best),
                    },
                    evidence={
                        "slice_nloc": best.slice_nloc,
                        "ccn_removed": best.ccn_removed,
                    },
                    impact_delta=round(float(impact), 3),
                    effort_bucket=effort_bucket(best.slice_nloc),
                    blast_radius={"scope": "local"},
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
    def _suggested_name(analysis: FunctionAnalysis, extraction: Extraction) -> str | None:
        """A deterministic starting name for the lifted helper.

        Same posture as Extract Helper (see ``naming``): anchor the name to
        something the plan already knows rather than guess what the block does.
        The slice's OUT value is that anchor when there is exactly one -- a span
        whose single product is ``average`` is, by construction, the code that
        computes it, so ``compute_average`` describes it without inferring
        intent. With no single OUT (a void slice, or several) the only certain
        anchor left is the function the span came out of, which at least names
        the helper for its context. Measured over the 854 stored plans on this
        repo's index, 545 (64%) come from the OUT value and 309 from the
        enclosing function.

        **Not unique within a file, by design.** Two functions in one file can
        each produce a value with the same name, and both spans then get the
        same ``compute_*``: 28 of those 854 plans, across 14 files, collide with
        a sibling that way (the fallback branch does not collide, since there is
        one plan per function). Uniqueness would need a per-file suffix, which
        would renumber existing names whenever a new plan appeared and churn
        every persisted row. The name is a starting point every surface frames
        as editable, so the surfaces say to rename on a clash instead.
        """
        if len(extraction.returns) == 1:
            slug = identifier_slug(extraction.returns[0])
            if slug and slug not in _UNINFORMATIVE_OUT:
                return f"compute_{slug}"
        return None

    @staticmethod
    def _confidence(extraction: Extraction) -> str:
        """High when the extraction is unambiguous -- it removes several decision
        points with a clean signature; medium otherwise. (Every emitted span is
        single-exit with at most one return by construction.)"""
        if extraction.ccn_removed >= 2 and len(extraction.params) <= 4:
            return "high"
        return "medium"
