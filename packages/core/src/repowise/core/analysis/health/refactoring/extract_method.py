"""Extract Method detector -- the first dataflow-driven refactoring.

When a function is flagged ``large_method`` / ``brain_method`` /
``complex_method``, the dataflow layer (CFG + def/use + reaching definitions)
finds a contiguous statement span that can be lifted into a helper without
changing behaviour, and infers that helper's signature (IN parameters,
OUT return). This detector turns the best such span into one structured
``RefactoringSuggestion`` per flagged function.

``impact_delta`` is what the extraction itself recovers, not the whole
finding. The residual method keeps ``ccn - ccn_removed`` decision points and
``nloc - slice_nloc + 1`` lines (the call replaces the span), the new helper
carries ``ccn_removed + 1`` and ``slice_nloc``, and each is re-graded with the
source biomarker's own severity rule. The finding's impact is credited in
proportion to the severity deduction that disappears: all of it when both
shapes fall below the biomarker's bar, the band difference when the residual
only drops a band, and nothing when a CCN 209 method sheds 6 and stays
critical. A function that needs several extractions therefore shows several
partial steps rather than one step claiming the full finding.

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

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..biomarkers.brain_method import BrainMethodDetector
from ..biomarkers.complex_method import ComplexMethodDetector
from ..biomarkers.large_method import LargeMethodDetector
from ..complexity.languages import get_language_map
from ..dataflow import find_extractions
from ..scoring import severity_deduction
from .models import RefactoringContext, RefactoringSuggestion
from .naming import identifier_slug
from .registry import RefactoringDetector, effort_bucket, register

if TYPE_CHECKING:
    from ..dataflow import Extraction, FunctionAnalysis
    from ..models import Severity

# OUT values whose name describes the variable's role, not the block's
# product: ``compute_result`` names nothing the reader did not know.
_UNINFORMATIVE_OUT = frozenset(
    {"out", "result", "results", "value", "values", "ret", "tmp", "temp", "data", "item"}
)

# The function-level structural biomarkers this detector answers. A function is
# only offered an extraction when one of these flagged it, so the suggestion
# list never exceeds (and stays consistent with) what health surfaces.
_SOURCE_BIOMARKERS = ("brain_method", "large_method", "complex_method")

# Each source biomarker's own ``(ccn, nloc) -> severity`` rule.
_SEVERITY_RULE: dict[str, Callable[[int, int], Severity | None]] = {
    "brain_method": BrainMethodDetector.severity_for,
    "large_method": LargeMethodDetector.severity_for,
    "complex_method": ComplexMethodDetector.severity_for,
}


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
            matched = self._findings_for(analysis, ctx.findings)
            if not matched:
                # Only suggest where a method biomarker actually fired.
                continue
            candidates = find_extractions(analysis, lmap)
            if not candidates:
                continue
            best = candidates[0]  # already best-first
            impact, source = self._impact_for(analysis, best, matched)
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
    def _findings_for(analysis: FunctionAnalysis, findings: list[Any]) -> list[Any]:
        """The file's method-smell findings on *analysis*. Matches by function
        name and line containment so the right finding is picked when a name
        repeats."""
        out = []
        for f in findings:
            if getattr(f, "biomarker_type", "") not in _SOURCE_BIOMARKERS:
                continue
            if getattr(f, "function_name", "") != analysis.name:
                continue
            line = getattr(f, "line_start", None)
            if line is not None and not (analysis.start_line <= line <= analysis.end_line):
                continue
            out.append(f)
        return out

    @staticmethod
    def _impact_for(
        analysis: FunctionAnalysis, extraction: Extraction, findings: list[Any]
    ) -> tuple[float, str]:
        """Impact *extraction* recovers + the source biomarker it recovers it
        from. The finding recovering the most wins; when none recovers
        anything, the largest finding still names the cause."""
        best: tuple[float, float, str] = (-1.0, -1.0, "")
        for f in findings:
            biomarker = getattr(f, "biomarker_type", "")
            full = float(getattr(f, "health_impact", 0.0) or 0.0)
            recovered = full * _recovered_fraction(biomarker, analysis, extraction)
            best = max(best, (recovered, full, biomarker))
        return best[0], best[2]

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


def _recovered_fraction(
    biomarker: str, analysis: FunctionAnalysis, extraction: Extraction
) -> float:
    """Share of *biomarker*'s deduction that applying *extraction* removes.

    Both post-extraction shapes (the residual method and the new helper) are
    graded with the biomarker's own rule; whatever deduction they still earn
    is subtracted. Brain Method's file-level centrality gate is unchanged by a
    local extraction, so only its size/complexity bar is re-checked.
    """
    rule = _SEVERITY_RULE[biomarker]
    before = rule(analysis.ccn, analysis.nloc)
    if before is None:
        # These metrics do not reproduce the finding, so there is no band to
        # re-grade against; keep the finding's own impact.
        return 1.0
    residual = rule(
        analysis.ccn - extraction.ccn_removed, analysis.nloc - extraction.slice_nloc + 1
    )
    helper = rule(extraction.ccn_removed + 1, extraction.slice_nloc)
    remaining = sum(severity_deduction(s) for s in (residual, helper) if s is not None)
    return max(0.0, 1.0 - remaining / severity_deduction(before))
