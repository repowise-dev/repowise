"""Fix-first units for stored refactoring plans."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.health.queue.eligibility import UNAUDITED_KINDS
from repowise.core.analysis.health.queue.order import LEVEL_RANK
from repowise.core.analysis.health.rows import field
from repowise.core.analysis.health.scoring import biomarker_dimension
from repowise.core.analysis.health.worth import low_priority, magnitude

from . import text
from ._files import (
    _action,
    _Files,
    _finish_shaped,
    _health_gain,
    _opportunity_call,
    _ShapeRank,
    _size_facts,
)
from ._parts import (
    _FUNCTION_LEADS,
    MAX_FACTS,
    SIZE_BREAK_UP,
    _one_of,
    _risk,
    _step_command,
    _Unit,
    _verify,
)
from .model import (
    FIX_EFFORTS,
    FIX_IMPROVES,
    FixConfidence,
    FixEffortEstimate,
    FixFact,
    FixSource,
    FixStep,
    FixTarget,
)
from .plan_steps import (
    _above_bar,
    _evidence,
    _extraction_worth,
    _plan_body,
    _refactor_step,
    _span,
    _stage_count,
)


def _refactor_unit(
    row: Any,
    details: Mapping[str, Any],
    steps: list[Mapping[str, Any]],
    gain: float,
    plans: Mapping[str, Any],
    files: _Files,
) -> _Unit:
    plan = _RefactorPlan.read(row, details, steps, gain, plans, files)
    dimension = biomarker_dimension(plan.marker) if plan.marker else "maintainability"
    return _finish_shaped(
        plan.rank,
        kind="refactor",
        source_id=field(row, "opportunity_id"),
        ready=plan.mechanical or plan.confidence == "high",
        confidence=plan.confidence,
        effort=plan.effort,
        improves=_one_of(dimension, FIX_IMPROVES, "maintainability"),
        fields=lambda: _refactor_fields(plan),
    )


@dataclass(frozen=True, slots=True)
class _RefactorPlan:
    """A stored refactoring plan as its item reads it: the lead step, the
    steps an item may carry, and the shape the plan is ranked on."""

    row: Any
    details: Mapping[str, Any]
    lead: Mapping[str, Any]
    steps: list[Mapping[str, Any]]
    held_back: int
    plans: Mapping[str, Any]
    files: _Files
    sym: str
    lead_type: str
    marker: str | None
    confidence: str
    effort: str
    rank: _ShapeRank

    @classmethod
    def read(
        cls,
        row: Any,
        details: Mapping[str, Any],
        steps: list[Mapping[str, Any]],
        gain: float,
        plans: Mapping[str, Any],
        files: _Files,
    ) -> _RefactorPlan:
        path = field(row, "file_path")
        lead = steps[0]
        steps, held_back = _audited_steps(steps)
        lead_type, marker = _lead_kind(row, lead)
        shape = files.shape(path, lead.get("target_symbol"))
        deps = details.get("dependents")
        deps = files.dependents(path) if deps is None else deps
        function_size = lead_type == "extract_method"
        credited, central = files.reach(path, shape, deps, sized=function_size)
        rank = _ShapeRank(
            shape,
            central,
            files.churning(path),
            changes=files.changes(
                path, lead.get("target_symbol"), function=lead_type in _FUNCTION_LEADS
            ),
            cloned=function_size and files.cloned(path, shape),
            low=low_priority(marker, shape, function_size=function_size) or _above_bar(details),
            gain=gain,
            ccn_removed=sum(_extraction_worth(plans.get(s.get("plan_id")))[0] for s in steps),
            dependents=deps,
            credited=credited,
        )
        return cls(
            row,
            details,
            lead,
            steps,
            held_back,
            plans,
            files,
            sym=text.short_symbol(lead.get("target_symbol")) or text.basename(path),
            lead_type=lead_type,
            marker=marker,
            confidence=_one_of(field(row, "confidence") or "medium", LEVEL_RANK, "medium"),
            effort=_one_of(field(row, "effort_bucket") or "M", FIX_EFFORTS, "M"),
            rank=rank,
        )

    @property
    def path(self) -> str:
        return field(self.row, "file_path")

    @property
    def mechanical_n(self) -> int:
        return sum(
            (s.get("applicability") or {}).get("classification") == "mechanical"
            for s in self.steps
        )

    @property
    def mechanical(self) -> bool:
        return self.mechanical_n == len(self.steps)


def _lead_kind(row: Any, lead: Mapping[str, Any]) -> tuple[str, str | None]:
    """The lead step's refactoring type, and the marker the plan answers."""
    lead_type = lead.get("refactoring_type") or field(row, "lead_refactoring_type") or ""
    marker = field(row, "lead_biomarker") or (
        lead_type if lead_type in ("split_file", "break_cycle") else None
    )
    return lead_type, marker


def _refactor_fields(plan: _RefactorPlan) -> dict[str, Any]:
    lead, path, rank, files = plan.lead, plan.path, plan.rank, plan.files
    lead_plan = plan.plans.get(lead.get("plan_id"))
    profiles = {p.get("id"): p for p in plan.details.get("validation_profiles") or []}
    verify = _verify(
        profiles.get(lead.get("validation_profile_id")) or next(iter(profiles.values()), None)
    )
    fix_steps = _refactor_fix_steps(plan, profiles, verify.command)
    n_steps, mechanical_n = text.plural(len(plan.steps), "step"), plan.mechanical_n
    # The symbol the lead step edits; ``None`` for a file-level plan.
    symbol = plan.sym if lead.get("target_symbol") else None
    return {
        "title": text.clip(_refactor_title(plan, lead_plan)),
        # The symbol's own span, as a finding's target gives it; where the
        # first edit lands is the first step's line.
        "target": FixTarget(
            path,
            symbol,
            lead.get("line_start") or fix_steps[0].line,
            lead.get("line_end"),
        ),
        "why": files.why(
            path,
            measured=_refactor_measure(plan.lead_type, plan.sym, path, lead, lead_plan, files),
            fallback=text.problem(plan.marker, plan.sym),
            dependents=rank.dependents,
            cloned=rank.cloned,
            changes=rank.changes,
            function=symbol is not None,
        ),
        "facts": _refactor_facts(plan, function=symbol is not None),
        "action": _action(f"{n_steps}, {mechanical_n} mechanical", fix_steps, plan.mechanical),
        "gain": _health_gain(rank.gain, ceiling=False),
        "effort": FixEffortEstimate(plan.effort, "sized by the stored plan"),
        "risk": _risk(
            int(field(plan.row, "affected_files_total") or 1),
            rank.dependents,
            function=symbol is not None,
        ),
        "confidence": FixConfidence(
            plan.confidence, f"{mechanical_n} of {n_steps} proven mechanical by the plan"
        ),
        "verify": verify,
        "context": files.context(path, symbol),
        "source": FixSource(
            field(plan.row, "opportunity_id"),
            tuple(s.get("plan_id") for s in plan.steps if s.get("plan_id")),
            tuple(plan.details.get("lead_finding_ids") or ()),
        ),
        "next_call": _opportunity_call(
            "The full plan: ordered steps, validation and evidence",
            field(plan.row, "opportunity_id"),
        ),
    }


def _refactor_fix_steps(
    plan: _RefactorPlan, profiles: Mapping[Any, Mapping[str, Any]], item_command: str | None
) -> tuple[FixStep, ...]:
    return tuple(
        _refactor_step(
            i + 1,
            s,
            plan.plans.get(s.get("plan_id")),
            _step_command(profiles.get(s.get("validation_profile_id")), item_command),
        )
        for i, s in enumerate(plan.steps)
    )


def _refactor_title(plan: _RefactorPlan, lead_plan: Any) -> str:
    """A staged plan names how many helpers it splits the function into, a big
    function's first lift names the problem and the span, and any other plan
    reads its title off the lead step's kind."""
    stages = _stage_count(_plan_body(lead_plan)) if plan.lead_type == "extract_method" else 0
    if stages:
        return _with_more_steps(plan, _staged_title(plan.sym, stages))
    return _span_title(plan, lead_plan)


def _staged_title(sym: str, stages: int) -> str:
    return f"Split {sym} into {stages} helpers"


def _span_title(plan: _RefactorPlan, lead_plan: Any) -> str:
    start, end = _span(lead_plan)
    # Titles never quote the helper name: it is a placeholder the step asks to rename.
    spanned = plan.lead_type == "extract_method" and bool(start and end)
    if spanned and magnitude(plan.rank.shape) >= SIZE_BREAK_UP:
        return _break_up_title(plan.sym, plan.rank.shape, f"{start}-{end}")
    template = text.REFACTOR_TITLE.get(
        "extract_method_span" if spanned else plan.lead_type, "Refactor {file}"
    )
    title = template.format(sym=plan.sym, file=text.basename(plan.path), start=start, end=end)
    return _with_more_steps(plan, title)


def _with_more_steps(plan: _RefactorPlan, title: str) -> str:
    if len(plan.steps) > 1:
        title += f" (+{text.plural(len(plan.steps) - 1, 'more step')})"
    return title


def _break_up_title(sym: str, shape: Mapping[str, int], span: str, suffix: str = "") -> str:
    title = f"Start breaking up {sym} ({text.size_brief(shape)}): first lift lines {span}"
    # A caveat outranks the span and the size, which the step and why name anyway.
    head = f"Start breaking up {sym}"
    for candidate in (title, f"{head} ({text.size_brief(shape)})", head):
        if len(candidate + suffix) <= text.TITLE_MAX:
            return candidate + suffix
    return head + suffix


def _refactor_facts(plan: _RefactorPlan, *, function: bool) -> tuple[FixFact, ...]:
    rank, held_back = plan.rank, plan.held_back
    facts = [
        FixFact("health recoverable", f"+{rank.gain:.1f}", "inferred"),
        *_size_facts(rank.shape),
        FixFact("steps", f"{len(plan.steps)} ({plan.mechanical_n} mechanical)"),
        *([FixFact("steps held back", text.held_back(held_back))] if held_back else []),
        *plan.files.common_facts(plan.path, rank.dependents, function=function),
    ]
    nloc = plan.files.nloc(plan.path)
    if nloc:
        facts.append(FixFact("file size", f"{nloc} lines"))
    return tuple(facts[:MAX_FACTS])


def _audited_steps(steps: list[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], int]:
    """The steps an item may carry, and how many it held back: steps of a
    kind not yet audited stay in the full plan, never in the item."""
    kept = [s for s in steps if s.get("refactoring_type") not in UNAUDITED_KINDS]
    return kept, len(steps) - len(kept)


def _refactor_measure(
    kind: str, sym: str, path: str, step: Mapping[str, Any], plan: Any, files: _Files
) -> str | None:
    """The numbers behind a refactoring, from its plan evidence or its findings."""
    evidence = _evidence(plan)
    name = text.basename(path)
    if kind == "split_file" and evidence.get("file_nloc"):
        return (
            f"{name}: {text.plural(int(evidence['file_nloc']), 'line')} in "
            f"{evidence.get('group_count') or 'several'} loosely coupled groups"
        )
    if kind == "break_cycle" and evidence.get("cycle_size"):
        idiom = " within one directory (idiomatic)" if evidence.get("idiom") else ""
        return f"{name} is in an import cycle of {evidence['cycle_size']} files{idiom}"
    if kind == "extract_class" and evidence.get("method_count"):
        return (
            f"{sym}: {evidence['method_count']} methods in "
            f"{evidence.get('lcom4') or 'several'} groups that share little state"
        )
    if kind == "extract_helper" and evidence.get("duplicated_lines"):
        return (
            f"{sym}: {evidence['duplicated_lines']} lines duplicated across "
            f"{evidence.get('occurrence_count') or 2} places"
        )
    return text.measured(sym, files.shape(path, step.get("target_symbol")))
