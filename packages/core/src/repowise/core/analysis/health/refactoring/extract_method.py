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
lines, and not opening on the docstring. A miss drops the function's plan
rather than falling through, because the next span is then the same block
minus a statement, just under the cut.

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
  ``_suggested_name``). ``None`` when the span has no single informative OUT
  value: a name derived from the enclosing function described the context
  rather than the span, and collided with every sibling plan in the file.
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
from ..complexity.cyclomatic import _is_boolean_operator
from ..complexity.languages import get_language_map
from ..complexity.nloc import _is_docstring_stmt
from ..dataflow import find_extractions
from ..scoring import severity_deduction
from .models import RefactoringContext, RefactoringSuggestion
from .naming import join_identifier, split_words
from .registry import RefactoringDetector, effort_bucket, register

if TYPE_CHECKING:
    from ..complexity.languages import LanguageNodeMap
    from ..dataflow import Extraction, FunctionAnalysis
    from ..models import Severity

# OUT values whose name describes the variable's role, not the block's
# product: ``compute_result`` names nothing the reader did not know.
_UNINFORMATIVE_OUT = frozenset(
    {"out", "result", "results", "value", "values", "ret", "tmp", "temp", "data", "item"}
)

# How each language that reaches this detector joins the words of a helper
# name. Only the languages the Extract Method slicer has a dialect for
# (``dataflow/dialects/__init__.py``) can appear here. C++ is deliberately
# absent: it has no single convention (the standard library is snake_case,
# Google style is PascalCase, Qt is camelCase), so it keeps the snake_case
# default rather than getting one answer that is wrong for most C++ repos.
_NAME_CONVENTION: dict[str, str] = {
    "go": "camelCase",
    "java": "camelCase",
    "typescript": "camelCase",
    "tsx": "camelCase",
    "javascript": "camelCase",
    "jsx": "camelCase",
    "svelte": "camelCase",
    "vue": "camelCase",
}
_SNAKE_CASE = "snake_case"

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

# Minimum worth. Census of the 1,162 stored plans on this repo's index: the best
# span of 408 missed this floor (2 code lines lifting 2 decision points out of
# ``walk_file`` was the #1 plan), 187 of those had a next-best span that clears
# it and 221 were dropped. Table in the commit that set it.
_MIN_CCN_REMOVED = 2

# Offer gates on the chosen span, cut by a pre-registered rule over every stored
# plan of three repos: the loosest cell of share {0.60, 0.65, 0.70} x code lines
# {6, 8, 10} x decision points {2, 3} that drops each audited bad plan (a span
# holding 0.63-0.72 of the function, a 6-line span) and keeps each audited good
# one. One good plan sits at exactly 0.60, so the share bound is inclusive.
_MAX_SPAN_SHARE = 0.60
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
        for analysis in analyses:
            matched = self._findings_for(analysis, ctx.findings)
            if not matched:
                # Only suggest where a method biomarker actually fired.
                continue
            if jsx_plumbing_dominates(analysis.fn_node, lmap):
                continue
            markers = {getattr(f, "biomarker_type", "") for f in matched}
            # Best-first, so the first span worth doing is the strongest one.
            best = next(
                (
                    c
                    for c in find_extractions(analysis, lmap)
                    if _worth_extracting(analysis, c, markers)
                ),
                None,
            )
            if best is None or not _offerable(analysis, best):
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
                        "suggested_name": self._suggested_name(analysis, best, ctx.language),
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
        """A deterministic starting name for the lifted helper, in *language*'s
        identifier convention.

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

        Convention is per language: Python, Rust and C++ keep ``compute_average``;
        Go, Java and the TypeScript/JavaScript family take ``computeAverage``.
        C++ gets no convention because it has no single one -- the standard
        library is snake_case and Google style is PascalCase, so a fixed answer
        would be wrong for as many repos as it fixed. The out value's own casing
        is kept as word boundaries rather than thrown away, so ``meanValue`` is
        ``computeMeanValue`` in Java, not ``compute_meanvalue``.

        **Not unique within a file, by design.** Two functions in one file can
        each produce a value with the same name, and both spans then get the
        same ``compute_*``: 28 of those 854 plans, across 14 files, collide with
        a sibling that way (the fallback branch does not collide, since there is
        one plan per function). Uniqueness would need a per-file suffix, which
        would renumber existing names whenever a new plan appeared and churn
        every persisted row. The name is a starting point every surface frames
        as editable, so the surfaces say to rename on a clash instead.
        """
        if len(extraction.returns) != 1:
            return None
        out_words = split_words(extraction.returns[0])
        if not out_words:
            return None
        # ``_UNINFORMATIVE_OUT`` is keyed on the single-word slug, matching the
        # names it holds (``meanValue`` is a product, ``result`` is a role).
        if "_".join(out_words) in _UNINFORMATIVE_OUT:
            return None
        convention = _NAME_CONVENTION.get(language or "", _SNAKE_CASE)
        return join_identifier(["compute", *out_words], convention)

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

    Decision points for ``complex_method``, code lines for ``large_method``
    (the walker's NLOC rule on both sides), capped at 1. Per biomarker because
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
    return extraction.ccn_removed >= _MIN_CCN_REMOVED


def _offerable(analysis: FunctionAnalysis, extraction: Extraction) -> bool:
    """Whether the chosen span is a split worth offering; a miss drops the plan."""
    fn_lines = max(analysis.end_line - analysis.start_line + 1, 1)
    span_lines = extraction.end_line - extraction.start_line + 1
    if span_lines / fn_lines > _MAX_SPAN_SHARE or extraction.slice_nloc < _MIN_OFFER_NLOC:
        return False
    return not _starts_on_docstring(analysis.fn_node, extraction.start_line)


def _starts_on_docstring(fn_node: Any, start_line: int) -> bool:
    """True when the span opens on the body's docstring (or a JS/TS directive
    such as ``"use strict"``): lifting it strips the function of it. A leading
    comment is not a statement here, so a span after one is unaffected."""
    body = fn_node.child_by_field_name("body") if fn_node is not None else None
    first = next(iter(body.named_children), None) if body is not None else None
    return (
        first is not None and _is_docstring_stmt(first) and first.start_point[0] + 1 == start_line
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
        in_jsx = node.type.startswith("jsx_")
        has_jsx = has_jsx or in_jsx
        if (node.is_named and node.type in kinds) or _is_boolean_operator(node, lmap):
            total += 1
            plumbing += inside
        inside = inside or in_jsx
        stack.extend((child, inside) for child in node.children)
    return has_jsx and plumbing * 2 > total
