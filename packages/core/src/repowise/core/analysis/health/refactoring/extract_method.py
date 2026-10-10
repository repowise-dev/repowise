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
- ``plan.new_symbol`` = ``{"kind": "method" | "function" | None, "async":
  bool, "receiver": str | None, "uses_receiver": bool | None, "assigns":
  [str, ...] | None}`` -- whether the helper must be a method (the span uses
  ``self`` / ``this`` / ``super`` / the Go receiver, or may read a Java / C++
  field bare, ``uses_receiver`` None), the receiver name a method keeps, and
  the receiver fields the span assigns directly (``Extraction``). None where
  the language cannot tell. ``new_symbol.async`` is the canonical async key;
  ``needs_async`` stays for stored rows and their readers. ``plan.receiver_hazard`` is set only when the
  receiver cannot be shared as it is (``_receiver_hazard``), which makes the
  step a judgment call.
- ``new_symbol`` also carries ``params`` (``[{"name", "type", "mode": "in" |
  "inout"}]``, the receiver left out of a method's) and ``returns``
  (``[{"name", "type"}]``), each type read off the name's declaration (None
  without one), and ``signature_text``, the helper's header in the file's
  language; ``plan.call_site`` = ``{"replace_span": {"start", "end"},
  "new_text": str}`` is the statement replacing the span (``render``), and
  ``new_symbol.notes`` what they cannot say. Both texts are None when
  ``kind`` is; ``call_site`` also when an awaiting span sits in a host that
  is not async. A missing name reads ``<name>`` in them,
  and ``suggested_name`` is in the language's private form (Python ``_x``).
- ``evidence`` = ``{"slice_nloc": int, "ccn_removed": int}`` -- the size and
  complexity (code lines, decision points) the residual method sheds.
- ``plan.stages`` -- only on a staged plan (``dataflow.stages``): a function
  no single helper brings under the bar (CCN over twice a stage's cap, Python
  and TS / JS) is split into helpers called in order. Each stage carries the
  same keys as a single-span plan plus ``ccn``, ``nloc`` and
  ``context_params`` (the names it reads through ``plan.parameter_object``,
  the shared values' object, or None). The top-level keys repeat stage 1, so
  a reader of a single-span plan reads the first step; ``orchestrator`` gives
  the function's CCN before and after, and ``evidence`` sums the stages, so
  the plan is credited the whole drop rather than one span's share. Detail
  only (``render.list_plan``).
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
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..biomarkers.brain_method import BrainMethodDetector
from ..biomarkers.complex_method import ComplexMethodDetector
from ..biomarkers.large_method import LargeMethodDetector
from ..complexity.cyclomatic import _is_boolean_operator, is_markup
from ..complexity.languages import get_language_map
from ..complexity.nloc import is_string_stmt
from ..dataflow import Extraction, find_extractions, get_defuse_dialect
from ..dataflow.stages import STAGE_MAX_CCN, StagePlan, find_stages
from ..effort import effort_bucket
from ..perf.dialects import PERF_DIALECTS
from ..scoring import severity_deduction
from . import render
from .helper_naming import ScopeNames, helper_name, out_value_name
from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, register

if TYPE_CHECKING:
    from ..complexity.languages import LanguageNodeMap
    from ..dataflow import Definition, FunctionAnalysis
    from ..dataflow.dialects.base import BaseDefUseDialect, Receiver
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

# Above this no single helper of a stage's size brings the function under it.
_STAGE_MIN_CCN = 2 * STAGE_MAX_CCN + 1
# Keys a stage carries that the plan's own (stage 1) keys do not.
_STAGE_ONLY_KEYS = ("ccn", "nloc", "context_params")


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
        dialect = get_defuse_dialect(ctx.language or "")
        # Source order, so the first of two colliding plans keeps the name.
        for analysis in sorted(analyses, key=lambda a: (a.start_line, a.end_line)):
            matched = self._findings_for(analysis, ctx.findings)
            if not matched:
                # Only suggest where a method biomarker actually fired.
                continue
            if jsx_plumbing_dominates(analysis.fn_node, lmap):
                continue
            markers = {getattr(f, "biomarker_type", "") for f in matched}
            fn_node = analysis.fn_node
            receiver = dialect.receiver(fn_node, lmap) if dialect and fn_node else None
            best = _choose(analysis, find_extractions(analysis, lmap, receiver), markers)
            staged = _staged(analysis, best, lmap, receiver, ctx.language, markers)
            if staged is not None:
                out.append(
                    self._staged_suggestion(ctx, analysis, staged, matched, names, receiver)
                )
                continue
            if best is None:
                continue
            impact, share, source = self._impact_for(analysis, best, matched)
            name = render.private_name(
                ctx.language,
                names.claim(
                    analysis,
                    helper_name(analysis, best, lmap, ctx.language, names.imports(fn_node)),
                ),
            )
            async_fields = _async_fields(analysis, best, lmap, ctx.language)
            symbol = _render_fields(
                analysis,
                best,
                dialect,
                ctx.language,
                name,
                _symbol_fields(best, receiver),
                async_host=async_fields.get("async_host", True),
            )
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
                        "suggested_name": name,
                        **async_fields,
                        **symbol,
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

    def _staged_suggestion(
        self,
        ctx: RefactoringContext,
        analysis: FunctionAnalysis,
        staged: StagePlan,
        matched: list[Any],
        names: ScopeNames,
        receiver: Receiver | None,
    ) -> RefactoringSuggestion:
        """The plan for a staged split, credited the whole CCN drop."""
        total = Extraction(
            start_line=staged.stages[0].start_line,
            end_line=staged.stages[-1].end_line,
            params=(),
            returns=(),
            slice_nloc=staged.slice_nloc,
            ccn_removed=staged.ccn_removed,
        )
        impact, _share, source = self._impact_for(analysis, total, matched)
        return RefactoringSuggestion(
            refactoring_type=self.name,
            file_path=ctx.file_path,
            target_symbol=analysis.name,
            line_start=analysis.start_line,
            line_end=analysis.end_line,
            plan=_staged_fields(analysis, staged, ctx.language, names, receiver),
            evidence={"slice_nloc": total.slice_nloc, "ccn_removed": total.ccn_removed},
            impact_delta=round(float(impact), 3),
            effort_bucket=effort_bucket(total.slice_nloc),
            blast_radius={"scope": "local"},
            # Several edits to one function: a reviewer reads the plan first.
            confidence="medium",
            source_biomarker=source,
        )

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


def _staged(
    analysis: FunctionAnalysis,
    best: Extraction | None,
    lmap: LanguageNodeMap,
    receiver: Receiver | None,
    language: str | None,
    markers: set[str],
) -> StagePlan | None:
    """The staged split, when the function is too complex for one helper and
    the split lifts at least twice what the best single span does (several
    edits for a point or two more is not the better plan); each stage must
    clear the floor a single span does."""
    # Only languages with a staged renderer, each with the outputs one call binds.
    max_returns = render.staged_outputs(language)
    if max_returns is None or analysis.ccn < _STAGE_MIN_CCN:
        return None
    plan = find_stages(analysis, lmap, receiver, max_returns=max_returns)
    if not plan.stages or (best is not None and plan.ccn_removed < 2 * best.ccn_removed):
        return None
    if not all(_worth_extracting(analysis, x, markers) for x in plan.stages):
        return None
    return plan


def _staged_fields(
    analysis: FunctionAnalysis,
    staged: StagePlan,
    language: str | None,
    names: ScopeNames,
    receiver: Receiver | None,
) -> dict[str, Any]:
    """The staged plan: stage 1's keys, ``stages``, ``parameter_object`` and
    ``orchestrator`` (module docstring)."""
    lmap = get_language_map(language or "")
    dialect = get_defuse_dialect(language or "")
    obj = _parameter_object(analysis, staged, dialect, language, names)
    # Without an object (no free variable name for it) the values stay plain.
    context = staged.context if obj else ()
    imports = names.imports(analysis.fn_node)
    stages = []
    for x, local_imports in zip(staged.stages, staged.imports, strict=True):
        name = render.private_name(
            language, names.claim(analysis, helper_name(analysis, x, lmap, language, imports))
        )
        carried = [p for p in x.params if p in context]
        own = replace(x, params=tuple(p for p in x.params if p not in context))
        lead = (render.Slot(obj["var"], obj["name"]),) if obj and carried else ()
        async_fields = _async_fields(analysis, x, lmap, language)
        symbol = _render_fields(
            analysis,
            own,
            dialect,
            language,
            name,
            _symbol_fields(x, receiver),
            async_host=async_fields.get("async_host", True),
            leading=lead,
        )
        _add_stage_notes(symbol["new_symbol"], lead, carried, local_imports)
        stages.append(
            {
                "span": {"start": x.start_line, "end": x.end_line},
                "params": [s.name for s in lead] + list(own.params),
                "returns": list(x.returns),
                "suggested_name": name,
                **async_fields,
                **symbol,
                "ccn": x.ccn_removed + 1,
                "nloc": x.slice_nloc,
                "context_params": carried,
            }
        )
    head = {k: v for k, v in stages[0].items() if k not in _STAGE_ONLY_KEYS}
    orchestrator = {"ccn_before": analysis.ccn, "ccn_after": analysis.ccn - staged.ccn_removed}
    return {**head, "stages": stages, "parameter_object": obj, "orchestrator": orchestrator}


def _add_stage_notes(
    symbol: dict[str, Any],
    lead: tuple[render.Slot, ...],
    carried: list[str],
    local_imports: tuple[str, ...],
) -> None:
    """What a stage's texts cannot say: the values it reads off the parameter
    object, and the function-local imports it must repeat."""
    notes = list(symbol.get("notes") or [])
    if lead:
        reads = ", ".join(f"{lead[0].name}.{p}" for p in carried)
        notes.append(f"Read {', '.join(carried)} in the helper as {reads}.")
    if local_imports:
        notes.append(
            f"Import {', '.join(local_imports)} in the helper: the function imports "
            f"{'it' if len(local_imports) == 1 else 'them'} inside its body."
        )
    if notes:
        symbol["notes"] = notes


def _parameter_object(
    analysis: FunctionAnalysis,
    staged: StagePlan,
    dialect: BaseDefUseDialect | None,
    language: str | None,
    names: ScopeNames,
) -> dict[str, Any] | None:
    """The object carrying the stages' shared values, or None without one:
    its type and variable name, typed fields, the declaration and the
    statement building it just above stage 1's call."""
    if not staged.context:
        return None
    first = staged.stages[0].start_line
    probe = Extraction(first, first, staged.context, (), 0, 0)
    types = _declared_types(analysis, probe, dialect)
    fields = tuple(render.Slot(n, types.get(n)) for n in staged.context)
    # Every name the function writes or reads (a module global ``ctx`` too).
    def_use = analysis.def_use
    taken = {d.var for d in def_use.definitions}
    taken |= {u.name for bdu in def_use.blocks.values() for u in bdu.uses}
    taken |= {u.name for u in def_use.captured.reads}
    var = next((v for v in ("ctx", "context", "stage_ctx") if v not in taken), None)
    if var is None:
        return None
    name = names.claim(analysis, render.context_name(language, analysis.name))
    name = name or render.NAME_PLACEHOLDER
    texts = render.render_context(language or "", name, var, fields)
    if texts is None:
        return None
    return {
        "name": name,
        "var": var,
        "fields": [{"name": f.name, "type": f.type} for f in fields],
        "declaration_text": texts.declaration,
        "construct_text": texts.construct,
        "construct_before": first,
        "notes": list(texts.notes),
    }


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


def _symbol_fields(extraction: Extraction, receiver: Receiver | None) -> dict[str, Any]:
    """``new_symbol``: what the helper is (``kind`` method or function, None
    when the language cannot tell), ``async``, the ``receiver`` a method
    reaches its instance by, whether the span uses it (None: it may, by a
    bare field name), and the receiver fields it assigns directly
    (``assigns``, None when not all are known). ``receiver_hazard`` only when
    lifting the span changes what the receiver is (``_receiver_hazard``)."""
    uses, assigns = extraction.uses_receiver, extraction.receiver_assigns
    hazard = _receiver_hazard(receiver, uses, assigns)
    kind: str | None = None
    if receiver is not None and hazard != "receiver_unbound":
        kind = "method" if receiver.implicit or uses else "function"
    name = min(receiver.names) if kind == "method" and receiver is not None else None
    fields: dict[str, Any] = {
        "new_symbol": {
            "kind": kind,
            # Canonical async key; ``needs_async`` is kept for stored rows.
            "async": extraction.needs_async,
            "receiver": name,
            "uses_receiver": uses,
            "assigns": list(assigns) if assigns is not None else None,
        }
    }
    if hazard:
        fields["receiver_hazard"] = hazard
    return fields


def _render_fields(
    analysis: FunctionAnalysis,
    extraction: Extraction,
    dialect: BaseDefUseDialect | None,
    language: str | None,
    name: str | None,
    symbol: dict[str, Any],
    *,
    async_host: bool,
    leading: tuple[render.Slot, ...] = (),
) -> dict[str, Any]:
    """*symbol* with ``new_symbol`` given its typed ``params`` / ``returns``,
    ``signature_text`` and any ``notes``, plus ``call_site`` (``render``).
    Types are read off the retained tree at each name's declaration, only for
    this plan's few names; the texts are None when the helper's form is
    unknown, and ``call_site`` also when the host cannot await the helper.
    *leading* slots come first (a staged plan's parameter object)."""
    new_symbol = symbol["new_symbol"]
    types = _declared_types(analysis, extraction, dialect)
    # Only a Rust value moves when passed, so only Rust asks what is read later.
    after = _read_after(analysis, extraction) if language == "rust" else set()
    # A method reaches its instance through the receiver, not an argument.
    own = new_symbol["receiver"] if new_symbol["kind"] == "method" else None
    params = leading + tuple(
        render.Slot(p, types.get(p), p in after) for p in extraction.params if p != own
    )
    returns = tuple(render.Slot(r, types.get(r)) for r in extraction.returns)
    declared, before, rebound = _out_binding(analysis, extraction, get_language_map(language or ""))
    fn_node = analysis.fn_node
    texts = render.render(
        render.HelperShape(
            language=language or "",
            name=name,
            kind=new_symbol["kind"],
            is_async=extraction.needs_async,
            params=params,
            returns=returns,
            receiver=new_symbol["receiver"],
            receiver_decl=dialect.receiver_decl(fn_node) if dialect and fn_node else None,
            async_host=async_host,
            out_declared=declared,
            out_written_before=before,
            out_rebound=rebound,
            typed_host=fn_node is not None
            and fn_node.child_by_field_name("return_type") is not None,
        )
    )
    rendered = {
        **new_symbol,
        "params": render.symbol_params(params, returns),
        "returns": [{"name": r.name, "type": r.type} for r in returns],
        "signature_text": texts.signature if texts else None,
    }
    if texts and texts.notes:
        rendered["notes"] = list(texts.notes)
    span = {"start": extraction.start_line, "end": extraction.end_line}
    call = texts.call if texts else None
    return {
        **symbol,
        "new_symbol": rendered,
        "call_site": {"replace_span": span, "new_text": call} if call else None,
    }


def _declared_types(
    analysis: FunctionAnalysis, extraction: Extraction, dialect: BaseDefUseDialect | None
) -> dict[str, str]:
    """Each IN / OUT name's declared type, from its latest typed write at or
    before the span's end (a parameter, a typed declaration). Definitions are
    numbered by CFG block, not source order, so they are sorted first."""
    fn_node = analysis.fn_node
    wanted = set(extraction.params) | set(extraction.returns)
    if dialect is None or fn_node is None or not wanted:
        return {}
    writes = sorted(
        (d for d in analysis.def_use.definitions if d.var in wanted and d.line <= extraction.end_line),
        key=lambda d: (d.line, d.column),
        reverse=True,
    )
    found: dict[str, str] = {}
    for d in writes:
        if d.var in found:
            continue
        raw = d.var.encode()
        row = d.line - 1
        # Tree points are byte columns.
        node = fn_node.descendant_for_point_range((row, d.column), (row, d.column + len(raw)))
        if node is not None and node.text == raw:
            typ = dialect.declared_type(node)
            if typ:
                found[d.var] = typ
    return found


def _read_after(analysis: FunctionAnalysis, extraction: Extraction) -> set[str]:
    """The IN names the function reads after the span (a closure's read counts
    where the closure is written)."""
    wanted, e = set(extraction.params), extraction.end_line
    def_use = analysis.def_use
    uses = [u for bdu in def_use.blocks.values() for u in bdu.uses if not u.echo]
    return {u.name for u in (*uses, *def_use.captured.reads) if u.name in wanted and u.line > e}


def _out_binding(
    analysis: FunctionAnalysis, extraction: Extraction, lmap: LanguageNodeMap | None
) -> tuple[bool, bool, bool]:
    """Whether the span's output is declared in it (the call must declare it:
    a declaration among the span's own statements, or no write before the
    span), whether it was written before the span, and whether it is written
    again after it."""
    if not extraction.returns:
        return False, False, False
    out, s, e = extraction.returns[0], extraction.start_line, extraction.end_line
    writes = [d for d in analysis.def_use.definitions if d.var == out]
    before = any(d.line < s for d in writes)
    declared = (
        any(
            s <= d.line <= e and d.declares and _top_level_declaration(analysis, d, s, e, lmap)
            for d in writes
        )
        or not before
    )
    return declared, before, any(d.line > e for d in writes)


def _top_level_declaration(
    analysis: FunctionAnalysis, d: Definition, s: int, e: int, lmap: LanguageNodeMap | None
) -> bool:
    """Whether declaration *d* is one of the span's own statements. One in a
    block nested in the span (or a loop header binder) binds only there, so
    the outer name the call writes is not declared by it. True when the tree
    or the language map is missing (the old answer)."""
    fn_node = analysis.fn_node
    if fn_node is None or lmap is None:
        return True
    row = d.line - 1
    node = fn_node.descendant_for_point_range((row, d.column), (row, d.column + len(d.var.encode())))
    while node is not None and node.parent is not None and node.parent.type not in lmap.block_kinds:
        node = node.parent
    if node is None or node.parent is None or node.type not in lmap.local_decl_kinds:
        return False
    # The span's statements are siblings in one block: it starts at s, ends at e.
    rows = [(k.start_point[0] + 1, k.end_point[0] + 1) for k in node.parent.named_children]
    return any(lo == s for lo, _ in rows) and any(hi == e for _, hi in rows)


def _receiver_hazard(
    receiver: Receiver | None, uses: bool | None, assigns: tuple[str, ...] | None
) -> str | None:
    """Why a helper cannot share the span's receiver as it is, or None.

    ``receiver_unbound``: the span uses ``this`` where it is not a class
    instance (a TS/JS plain function or object-literal method), so no helper
    method can be given the same one. ``receiver_copy_written``: the span
    assigns a field of a Go value receiver, a copy, so a helper holding its
    own copy loses the write the rest of the method reads (also when the
    assigned fields are unknown).
    """
    if receiver is None or not uses:
        return None
    if not receiver.bound:
        return "receiver_unbound"
    if receiver.copy and (assigns is None or assigns):
        return "receiver_copy_written"
    return None


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
