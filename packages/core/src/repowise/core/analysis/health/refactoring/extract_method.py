"""Extract Method detector -- the first dataflow-driven refactoring.

When a function is flagged ``large_method`` / ``brain_method`` /
``complex_method``, the dataflow layer (CFG + def/use + reaching definitions)
finds a contiguous statement span that can be lifted into a helper without
changing behaviour, and infers that helper's signature (IN parameters,
OUT return). This detector turns the best such span into one structured
``RefactoringSuggestion`` per flagged function.

``impact_delta`` is the share of the finding the extraction itself removes,
not the whole finding: the share of what the finding measures that moves into
the helper, applied to the finding's ``health_impact``. That is
``ccn_removed / ccn`` for ``complex_method`` and ``brain_method``, and the
smaller of ``slice_nloc / nloc`` and that for ``large_method``
(:func:`recovered_share`). Both line counts follow the walker's NLOC rule, so
comments credit nothing. Crediting the whole finding whenever the residual and
the helper both fell under the biomarker's bar let a two-line span in a
function barely over the bar claim all of it, and rank first on the repo.

A span is only offered when it is worth doing (:func:`_worth_extracting`): it
removes at least two decision points and the helper it creates would not carry
the finding at the function's own severity or worse (lifting nearly the whole
body moves the smell, it does not split it). When the best span misses that
floor the next-best one that clears it is offered instead.

That span is then offered only when it is a split a reviewer would make
(:func:`_offerable`): at most 60% of the function's lines, at least 8 code
lines, and not opening on the docstring. A miss falls through only to a span
that does not overlap it: an overlapping one is the same block minus a
statement, just under the cut.

A JSX function component whose decision points sit mostly in its markup
(conditional spreads, ``&&`` and ternaries in attributes or children, template
ternaries, all under a ``jsx_*`` node) is not offered one: that branching is prop plumbing, and a helper
would only move it. CCN is left alone because it feeds the calibrated defect
score; the gate is on eligibility.

The candidate spans + IN/OUT come from ``dataflow.find_extractions``; this
module only matches each analysed function to the biomarker finding that flags
it (for the recovered impact), picks the strongest extraction, and renders the
plan. Precision-first: a function with no safe, complexity-removing span yields
no suggestion.

Plan shape (open dict, no migration):

- ``plan`` = ``{"span": {"start": int, "end": int}, "params": [str, ...],
  "returns": [str, ...], "suggested_name": str | None}`` -- the lines to lift,
  the inferred signature, and a deterministic starting name (see
  ``helper_naming``): a ``timed()`` stage label, a banner comment, or
  ``compute_<out>`` for an effect-free span with one informative OUT value.
  ``None`` when nothing anchors a name, or when the name is already taken in
  the scope the helper lands in.
- ``plan.needs_async`` -- the span awaits, so the helper is async and its call
  is awaited. An awaiting plan also carries ``async_host``: False when the
  enclosing function is not declared async, which makes the step a judgment
  call (``preconditions``) rather than a mechanical one.
- ``evidence`` = ``{"slice_nloc": int, "ccn_removed": int}`` -- the size and
  complexity (code lines, decision points) the residual method sheds.
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
from ..complexity.cyclomatic import _is_boolean_operator, is_markup
from ..complexity.languages import get_language_map
from ..complexity.nloc import is_string_stmt
from ..dataflow import find_extractions
from ..effort import effort_bucket
from ..perf.dialects import PERF_DIALECTS
from ..scoring import severity_deduction
from .helper_naming import ScopeNames, helper_name, out_value_name
from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, register

if TYPE_CHECKING:
    from ..complexity.languages import LanguageNodeMap
    from ..dataflow import Extraction, FunctionAnalysis
    from ..models import Severity

# The function-level structural biomarkers this detector answers. A function is
# only offered an extraction when one of these flagged it, so the suggestion
# list never exceeds (and stays consistent with) what health surfaces.
_SOURCE_BIOMARKERS = ("brain_method", "large_method", "complex_method")

# Each source biomarker's own ``(ccn, nloc) -> severity`` rule, for the helper.
_SEVERITY_RULE: dict[str, Callable[[int, int], Severity | None]] = {
    "brain_method": BrainMethodDetector.severity_for,
    "large_method": LargeMethodDetector.severity_for,
    "complex_method": ComplexMethodDetector.severity_for,
}

# Minimum worth: a helper lifting one decision point is a renamed ``if``, not
# a split, however many straight lines come with it.
_MIN_OFFER_CCN_REMOVED = 2

# A span over three fifths of the function leaves a shell (a guard, a wrapper,
# the final ``return``) behind; the smell moved instead of splitting. Inclusive,
# since a span at exactly 0.60 still leaves real work. This is a stricter
# offer-time gate on the function's physical lines, distinct from slice.py's
# candidate-time ``_MAX_BODY_SHARE`` on the body's code lines.
_MAX_SPAN_SHARE = 0.60
# Below 8 code lines the helper's call and signature cost about what it saves.
_MIN_OFFER_NLOC = 8

# ``high`` confidence also needs the helper to take a real share of the
# function: below a tenth (a 2-point span out of a CCN 249 function) the step
# is safe but moves almost nothing.
_HIGH_MIN_SHARE = 0.1


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
        names = ScopeNames(lmap)
        # Source order, so the first of two colliding plans keeps the name.
        for analysis in sorted(analyses, key=lambda a: (a.start_line, a.end_line)):
            matched = self._findings_for(analysis, ctx.findings)
            if not matched:
                # Only suggest where a method biomarker actually fired.
                continue
            if jsx_plumbing_dominates(analysis.fn_node, lmap):
                continue
            markers = {getattr(f, "biomarker_type", "") for f in matched}
            best = _choose(analysis, find_extractions(analysis, lmap), markers)
            if best is None:
                continue
            impact, share, source = self._impact_for(analysis, best, matched)
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
                        "suggested_name": names.claim(
                            analysis,
                            helper_name(
                                analysis, best, lmap, ctx.language, names.imports(analysis.fn_node)
                            ),
                        ),
                        **_async_fields(analysis, best, lmap, ctx.language),
                    },
                    evidence={
                        "slice_nloc": best.slice_nloc,
                        "ccn_removed": best.ccn_removed,
                    },
                    impact_delta=round(float(impact), 3),
                    effort_bucket=effort_bucket(best.slice_nloc),
                    blast_radius={"scope": "local"},
                    confidence=self._confidence(best, share),
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
    ) -> tuple[float, float, str]:
        """Impact *extraction* recovers, its share of the finding, and the source
        biomarker. One finding is credited, the one recovering the most: the
        composer counts a finding once across steps."""
        best: tuple[float, float, float, str] = (-1.0, 0.0, -1.0, "")
        for f in findings:
            biomarker = getattr(f, "biomarker_type", "")
            full = float(getattr(f, "health_impact", 0.0) or 0.0)
            share = recovered_share(biomarker, analysis, extraction)
            best = max(best, (full * share, share, full, biomarker))
        return best[0], best[1], best[3]

    @staticmethod
    def _suggested_name(
        analysis: FunctionAnalysis, extraction: Extraction, language: str | None = None
    ) -> str | None:
        """The OUT-value name alone (:func:`helper_naming.out_value_name`)."""
        return out_value_name(analysis, extraction, language)

    @staticmethod
    def _confidence(extraction: Extraction, share: float) -> str:
        """High when the extraction is unambiguous and worth handing off: a
        clean signature and a real share of the function. Medium otherwise.
        (Every emitted span is single-exit with at most one return and removes
        at least two decision points by construction.)"""
        if len(extraction.params) <= 4 and share >= _HIGH_MIN_SHARE:
            return "high"
        return "medium"


def recovered_share(
    biomarker: str, analysis: FunctionAnalysis, extraction: Extraction
) -> float:
    """Share of what *biomarker* measures that *extraction* moves into the helper.

    Decision points for every biomarker except ``large_method``, the only one
    *biomarker* changes: code lines there (the walker's NLOC rule on both
    sides), capped at 1. Per biomarker because
    the plain maximum let 2 decision points out of a CCN 3, 292-line method
    claim two thirds of a size finding. Never more than the decision-point
    share, ``brain_method`` included: a long flat span claimed most of a size
    finding for moving straight-line code.
    """
    ccn_share = extraction.ccn_removed / analysis.ccn if analysis.ccn > 0 else 0.0
    if biomarker == "large_method":
        nloc_share = extraction.slice_nloc / analysis.nloc if analysis.nloc > 0 else 0.0
        return min(1.0, nloc_share, ccn_share)
    return min(1.0, ccn_share)


def _async_fields(
    analysis: FunctionAnalysis,
    extraction: Extraction,
    lmap: LanguageNodeMap,
    language: str | None,
) -> dict[str, bool]:
    """``needs_async`` always; ``async_host`` only for an awaiting span, False
    when the function holding it is not declared async (a C++ coroutine),
    where no async helper can be written in its place."""
    if not extraction.needs_async:
        return {"needs_async": False}
    return {"needs_async": True, "async_host": host_is_async(analysis.fn_node, lmap, language)}


def host_is_async(fn_node: Any, lmap: LanguageNodeMap, language: str | None) -> bool:
    """Whether *fn_node* is declared async, by the perf pass's own test: a
    dedicated async node kind, else the language's perf dialect."""
    if fn_node.type in lmap.async_function_kinds:
        return True
    dialect = PERF_DIALECTS.get(language or "")
    return dialect is not None and dialect.is_async_fn(fn_node)


def _worth_extracting(
    analysis: FunctionAnalysis, extraction: Extraction, markers: set[str]
) -> bool:
    """The minimum-worth floor, applied while choosing among candidates."""
    for marker in markers:
        rule = _SEVERITY_RULE[marker]
        helper = rule(extraction.ccn_removed + 1, extraction.slice_nloc)
        before = rule(analysis.ccn, analysis.nloc)
        # The helper inherits the finding undiminished: the smell moved.
        if helper is not None and (
            before is None or severity_deduction(helper) >= severity_deduction(before)
        ):
            return False
    return extraction.ccn_removed >= _MIN_OFFER_CCN_REMOVED


def _choose(
    analysis: FunctionAnalysis, candidates: list[Extraction], markers: set[str]
) -> Extraction | None:
    """The strongest span worth doing and offerable, best-first.

    Once the strongest worth-doing span is refused, only spans disjoint from it
    stay eligible: one overlapping it is a shrunken copy of the refused plan.
    """
    refused: Extraction | None = None
    for c in candidates:
        if not _worth_extracting(analysis, c, markers):
            continue
        if refused is not None and not (
            c.end_line < refused.start_line or c.start_line > refused.end_line
        ):
            continue
        if _offerable(analysis, c):
            return c
        refused = refused or c
    return None


def _offerable(analysis: FunctionAnalysis, extraction: Extraction) -> bool:
    """Whether a span is a split worth offering (share of physical lines)."""
    fn_lines = max(analysis.end_line - analysis.start_line + 1, 1)
    span_lines = extraction.end_line - extraction.start_line + 1
    if span_lines / fn_lines > _MAX_SPAN_SHARE or extraction.slice_nloc < _MIN_OFFER_NLOC:
        return False
    return not _starts_on_docstring(analysis.fn_node, extraction.start_line)


def _starts_on_docstring(fn_node: Any, start_line: int) -> bool:
    """True when the span opens on the body's docstring (or a JS/TS directive
    such as ``"use strict"``): lifting it strips the function of it. Comments
    before it are skipped, as they are not statements."""
    body = fn_node.child_by_field_name("body") if fn_node is not None else None
    if body is None:
        return False
    first = next((c for c in body.named_children if "comment" not in c.type), None)
    # Only bodies holding bare string statements (Python, JS/TS) can match.
    return (
        first is not None
        and is_string_stmt(first)
        and start_line <= first.start_point[0] + 1
    )


def jsx_plumbing_dominates(fn_node: Any, lmap: LanguageNodeMap) -> bool:
    """True for a function rendering JSX whose decision points sit mostly in it.

    Counts decision points the way the CCN walker does (nested functions are
    their own; arrow functions count toward this one) and the share of them
    inside the markup: under any ``jsx_*`` node, which covers spreads, template
    ternaries and ``&&`` in attributes and children. The same shapes outside
    the markup (``const cfg = {...defaults, x: a && b}``) are logic, not wiring.
    """
    if fn_node is None:
        return False
    body = fn_node.child_by_field_name("body") or fn_node
    kinds = lmap.branch_kinds | lmap.loop_kinds | lmap.case_kinds | lmap.catch_kinds
    total = plumbing = 0
    has_jsx = False
    stack = [(child, False) for child in body.children]
    while stack:
        node, inside = stack.pop()
        if node.type in lmap.function_kinds:
            continue
        in_jsx = is_markup(node)
        has_jsx = has_jsx or in_jsx
        if (node.is_named and node.type in kinds) or _is_boolean_operator(node, lmap):
            total += 1
            plumbing += inside
        inside = inside or in_jsx
        stack.extend((child, inside) for child in node.children)
    return has_jsx and plumbing * 2 > total
