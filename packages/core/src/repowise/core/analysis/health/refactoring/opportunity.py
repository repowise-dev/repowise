"""One file's refactoring, composed from that file's plans.

A detector output answers "here is a duplicated block" or "here is a liftable
span". Neither is the unit a person or an agent works in. The unit is a file:
what is wrong with it, what to do about it, in what order, and how much of that
can be handed off. This module folds the plans for one file into exactly that,
once, deterministically.

Three rules make the fold, and each lives with its owner. Membership and order
are here. Whether a step is safe to automate is :mod:`.preconditions`. What the
whole set is worth is :mod:`.opportunity_rank`. Identity reuses the plan kernels
in :mod:`.identity` rather than minting a second scheme, so an opportunity is
named by the work it contains and changes its name only when that work changes.

Two decisions are worth stating because they are not obvious:

**Order is structural first, then extractions.** A structural step relocates
symbols; a local extraction does not. Extracting first and splitting second
hands the split a symbol its grouping never saw, so the group it lands in is one
nobody computed. Doing it the other way round costs nothing: a lifted span
travels with its function.

**A low-signal clone is evidence, not a step.** Duplication is an observation.
Acting on it means creating a shared function and rewriting every call site,
which is only worth instructing when the sites really do move together - both
across files *and* co-changed. The rest still appears, attached to the file's
opportunity as the supporting evidence it always was, never as an instruction.
Nothing is deleted: the plans remain plans, addressable by id.

``performance_fix`` is excluded. Those rows belong to the performance layer,
which composes and ranks its own opportunities and owns their lifecycle; folding
them in here would publish the same work twice under two ids.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .extract_helper import ACTIVE_CO_CHANGE
from .identity import REFACTORING_MODEL_VERSION, assign_public_ids, stable_id
from .models import RefactoringSuggestion
from .opportunity_rank import (
    opportunity_benefit,
    opportunity_risk,
    rank_factors,
    rank_score,
    rank_sort_key,
    step_cost,
    weakest_confidence,
    why_ranked,
)
from .preconditions import StepApplicability, classify_step
from .recommendations import affected_files, rehydrate_suggestion

# Opportunity ids share the plan id's version and digest, and differ in the
# prefix alone, so a reader can tell the two units apart without a lookup and
# ``identity.model_state`` still reads the version straight off the string.
_PLAN_PREFIX = "refac"
_OPPORTUNITY_PREFIX = "refop"

# Owned by the performance layer end to end - see the module docstring.
EXCLUDED_TYPES = frozenset({"performance_fix"})

# Execution order. Lower runs first. Structural steps relocate symbols, so they
# precede the local extractions that would otherwise be re-grouped by them;
# within the structural block the widest change goes first, so a later step
# never has to be redone against a file an earlier one just moved.
STEP_ORDER: dict[str, int] = {
    "break_cycle": 0,
    "split_file": 1,
    "extract_class": 2,
    "move_method": 3,
    "extract_method": 4,
    "extract_helper": 5,
}
_UNORDERED = len(STEP_ORDER)

# Steps that move a symbol out of the file it is declared in. Anything ordered
# after one of these has to be located again before it can be applied: its
# ``file_path`` and span describe where the symbol was, not where the earlier
# step just put it. Line drift within one file is not in this set - an executor
# finds a symbol by name - but a wrong *file* sends them looking in the wrong
# place entirely.
_RELOCATING_TYPES = frozenset({"split_file", "extract_class", "move_method"})

_EFFORT_ORDER = ("S", "M", "L", "XL")

# Evidence restates the plan's own signals; these three are read-model
# additions the plan layer already publishes elsewhere.
_EVIDENCE_OMIT = frozenset({"provenance", "rank_factors", "rank_score"})


@dataclass(frozen=True, slots=True)
class OpportunityStep:
    """One plan, positioned in an execution order and classified for handoff.

    Carries the plan's identity and the facts a queue needs to render a row. The
    payload itself is not copied: the plan is addressable by ``plan_id``, and
    duplicating it here is how two representations of one plan start disagreeing.
    """

    plan_id: str
    refactoring_type: str
    target_symbol: str
    file_path: str
    line_start: int | None
    line_end: int | None
    effort_bucket: str
    confidence: str
    impact_delta: float
    source_biomarker: str
    applicability: StepApplicability
    # The earlier step in this opportunity that moves this step's symbol to
    # another file, so this one's location must be re-derived before it is
    # applied. ``None`` when nothing ahead of it relocates anything.
    relocated_by: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "refactoring_type": self.refactoring_type,
            "target_symbol": self.target_symbol,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "effort_bucket": self.effort_bucket,
            "confidence": self.confidence,
            "impact_delta": round(self.impact_delta, 3),
            "source_biomarker": self.source_biomarker,
            "relocated_by": self.relocated_by,
            "applicability": self.applicability.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class OpportunityEvidence:
    """An observation supporting the diagnosis without instructing a change."""

    plan_id: str
    refactoring_type: str
    target_symbol: str
    source_biomarker: str
    summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "refactoring_type": self.refactoring_type,
            "target_symbol": self.target_symbol,
            "source_biomarker": self.source_biomarker,
            "summary": dict(self.summary),
        }


@dataclass(frozen=True, slots=True)
class RefactoringOpportunity:
    """Everything one file needs done, ordered, with what backs it."""

    opportunity_id: str
    refactoring_model_version: int
    file_path: str
    lead_biomarker: str | None
    lead_refactoring_type: str
    # ``None`` when the file's dominant finding was not supplied - unknown,
    # never a quiet ``False``.
    addresses_primary_problem: bool | None
    steps: tuple[OpportunityStep, ...]
    evidence: tuple[OpportunityEvidence, ...]
    recoverable_health: float
    mechanical_steps: int
    judgment_steps: int
    affected_files: tuple[str, ...]
    effort_bucket: str
    confidence: str
    rank_score: float
    rank_factors: dict[str, float]
    why_ranked: tuple[dict[str, Any], ...]

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "refactoring_model_version": self.refactoring_model_version,
            "file_path": self.file_path,
            "lead_biomarker": self.lead_biomarker,
            "lead_refactoring_type": self.lead_refactoring_type,
            "addresses_primary_problem": self.addresses_primary_problem,
            "steps": [step.as_dict() for step in self.steps],
            "evidence": [item.as_dict() for item in self.evidence],
            "recoverable_health": round(self.recoverable_health, 3),
            "step_count": self.step_count,
            "mechanical_steps": self.mechanical_steps,
            "judgment_steps": self.judgment_steps,
            "affected_files": list(self.affected_files),
            "effort_bucket": self.effort_bucket,
            "confidence": self.confidence,
            "rank_score": self.rank_score,
            "rank_factors": dict(self.rank_factors),
            "why_ranked": [dict(entry) for entry in self.why_ranked],
        }


def is_standalone_clone(suggestion: RefactoringSuggestion) -> bool:
    """Whether a clone group earns a step rather than an evidence slot.

    Cross-file *and* co-change-backed: the sites sit in different files and git
    says they really are edited together. An intra-file repetition is a local
    tidy-up, and a cross-file pair nobody has ever changed twice is a coincidence
    of shape. Neither is worth instructing a rewrite of N call sites over.
    """
    evidence = suggestion.evidence or {}
    cross_file = not evidence.get("is_intra_file", False)
    co_change = evidence.get("co_change_count") or 0
    return cross_file and co_change >= ACTIVE_CO_CHANGE


def _is_step(suggestion: RefactoringSuggestion) -> bool:
    if suggestion.refactoring_type == "extract_helper":
        return is_standalone_clone(suggestion)
    return True


def _member_sort_key(item: tuple[RefactoringSuggestion, str]) -> tuple[int, int, str, str]:
    suggestion, plan_id = item
    return (
        STEP_ORDER.get(suggestion.refactoring_type, _UNORDERED),
        suggestion.line_start or 0,
        suggestion.target_symbol,
        plan_id,
    )


def _lead(
    steps: Sequence[OpportunityStep], primary_biomarker: str | None
) -> tuple[str | None, str, bool | None]:
    """The file's lead diagnosis, and whether a step actually addresses it.

    *primary_biomarker* is the file's dominant health finding, which the plans
    cannot supply: a file's worst problem is routinely one no detector answers
    (a coverage gap, churn) and asking the plans would only ever return a
    biomarker some plan already addresses, making the answer vacuously yes.
    When it is not supplied the question is unanswered, not answered ``False``.

    With no biomarker in play at all - the file's whole opportunity is
    structural, and structural detectors attribute to none - the first step is
    both the diagnosis and the answer to it.
    """
    if primary_biomarker is None:
        addressed = next((step for step in steps if step.source_biomarker), None)
        lead_type = (addressed or steps[0]).refactoring_type
        return (addressed.source_biomarker if addressed else None), lead_type, None
    addressing = next(
        (step for step in steps if step.source_biomarker == primary_biomarker), None
    )
    return primary_biomarker, (addressing or steps[0]).refactoring_type, addressing is not None


def _aggregate_effort(steps: Sequence[OpportunityStep]) -> str:
    """The set's effort: the largest single step, not the sum of the labels.

    Buckets are size classes, not durations, so adding them would produce a
    label no detector could emit. Total work enters the ranking through
    ``step_cost`` instead, where it belongs.
    """
    present = [step.effort_bucket for step in steps if step.effort_bucket in _EFFORT_ORDER]
    return max(present, key=_EFFORT_ORDER.index) if present else "M"


def opportunity_kernel(step_plan_ids: Sequence[str], file_path: str) -> tuple[Any, ...]:
    """Identity over the member plan ids, never a parallel scheme.

    The steps are what the opportunity instructs, so they are what names it, and
    each plan id is already the versioned digest of that plan's own kernel.
    Evidence is deliberately outside: a clone appearing or vanishing as a
    supporting observation must not rename the ordered work an agent is holding
    an id for.
    """
    return ("refactoring_opportunity", file_path, tuple(step_plan_ids))


def opportunity_public_id(step_plan_ids: Sequence[str], file_path: str) -> str:
    return stable_id(opportunity_kernel(step_plan_ids, file_path)).replace(
        _PLAN_PREFIX, _OPPORTUNITY_PREFIX, 1
    )


def _step_of(
    suggestion: RefactoringSuggestion, plan_id: str, relocated_by: str | None
) -> OpportunityStep:
    return OpportunityStep(
        plan_id=plan_id,
        refactoring_type=suggestion.refactoring_type,
        target_symbol=suggestion.target_symbol,
        file_path=suggestion.file_path,
        line_start=suggestion.line_start,
        line_end=suggestion.line_end,
        effort_bucket=suggestion.effort_bucket,
        confidence=suggestion.confidence,
        impact_delta=float(suggestion.impact_delta or 0.0),
        source_biomarker=suggestion.source_biomarker,
        relocated_by=relocated_by,
        applicability=classify_step(suggestion),
    )


def _evidence_of(suggestion: RefactoringSuggestion, plan_id: str) -> OpportunityEvidence:
    return OpportunityEvidence(
        plan_id=plan_id,
        refactoring_type=suggestion.refactoring_type,
        target_symbol=suggestion.target_symbol,
        source_biomarker=suggestion.source_biomarker,
        summary={
            key: value
            for key, value in (suggestion.evidence or {}).items()
            if key not in _EVIDENCE_OMIT
        },
    )


def _sequence(
    step_rows: Sequence[tuple[RefactoringSuggestion, str]],
) -> list[OpportunityStep]:
    """Build the steps in execution order, marking the ones a move displaces.

    Each marked step names the *most recent* relocation ahead of it, because
    that is the one whose result a reader has to look at to find the symbol
    again. It is deliberately coarse: it marks every later step, not only the
    ones whose own symbol moved, since which group a symbol landed in is not
    knowable from the plan payload and over-warning is the safe direction.
    """
    steps: list[OpportunityStep] = []
    relocated_by: str | None = None
    for suggestion, plan_id in step_rows:
        steps.append(_step_of(suggestion, plan_id, relocated_by))
        if suggestion.refactoring_type in _RELOCATING_TYPES:
            relocated_by = plan_id
    return steps


def _compose_one(
    file_path: str,
    members: Sequence[tuple[RefactoringSuggestion, str]],
    primary_biomarker: str | None,
) -> RefactoringOpportunity | None:
    ordered = sorted(members, key=_member_sort_key)
    step_rows = [row for row in ordered if _is_step(row[0])]
    if not step_rows:
        # Observations with nothing to instruct. The plans still stand on their
        # own; there is simply no composed refactoring to publish for this file.
        return None

    steps = tuple(_sequence(step_rows))
    evidence = tuple(
        _evidence_of(suggestion, plan_id)
        for suggestion, plan_id in ordered
        if not _is_step(suggestion)
    )

    step_suggestions = [suggestion for suggestion, _ in step_rows]
    touched = sorted({path for item in step_suggestions for path in affected_files(item)})
    biomarker, lead_type, addresses = _lead(steps, primary_biomarker)
    mechanical = sum(1 for step in steps if step.applicability.mechanical)
    factors = rank_factors(
        benefit=opportunity_benefit(step_suggestions),
        addresses_primary_problem=addresses is True,
        mechanical_share=mechanical / len(steps),
        cost=step_cost(step_suggestions),
        risk=opportunity_risk(step_suggestions, surface=max(0, len(touched) - 1)),
    )
    return RefactoringOpportunity(
        opportunity_id=opportunity_public_id([step.plan_id for step in steps], file_path),
        refactoring_model_version=REFACTORING_MODEL_VERSION,
        file_path=file_path,
        lead_biomarker=biomarker,
        lead_refactoring_type=lead_type,
        addresses_primary_problem=addresses,
        steps=steps,
        evidence=evidence,
        recoverable_health=sum(step.impact_delta for step in steps),
        mechanical_steps=mechanical,
        judgment_steps=len(steps) - mechanical,
        affected_files=tuple(touched),
        effort_bucket=_aggregate_effort(steps),
        confidence=weakest_confidence(step_suggestions),
        rank_score=rank_score(factors),
        rank_factors=factors,
        why_ranked=tuple(why_ranked(factors)),
    )


def compose_opportunities(
    rows: Iterable[Any],
    *,
    primary_biomarker_by_file: Mapping[str, str] | None = None,
) -> list[RefactoringOpportunity]:
    """Fold plans into one ranked opportunity per file.

    Accepts anything :func:`.recommendations.rehydrate_suggestion` accepts -
    detector dataclasses, dicts, ORM rows - so the same composition runs beside
    the analyzer and over rows read back out of storage, and cannot drift
    between the two. Pure: no I/O, no graph, no clock.

    *primary_biomarker_by_file* is the health layer's per-file dominant cause
    (:func:`..models.primary_biomarker_by_file`). Supplying it is what makes
    ``addresses_primary_problem`` a real question; omitting it leaves the answer
    explicitly unknown rather than assumed.
    """
    leads = primary_biomarker_by_file or {}
    suggestions = [
        suggestion
        for suggestion in (rehydrate_suggestion(row) for row in rows)
        if suggestion.refactoring_type not in EXCLUDED_TYPES
    ]
    plan_ids = assign_public_ids(suggestions)
    by_file: dict[str, list[tuple[RefactoringSuggestion, str]]] = {}
    for suggestion, plan_id in zip(suggestions, plan_ids, strict=True):
        by_file.setdefault(suggestion.file_path, []).append((suggestion, plan_id))

    composed = [
        opportunity
        for file_path in sorted(by_file)
        if (
            opportunity := _compose_one(file_path, by_file[file_path], leads.get(file_path))
        )
        is not None
    ]
    composed.sort(key=rank_sort_key)
    return composed


def opportunity_status(
    opportunity: RefactoringOpportunity, plan_status: Mapping[str, str]
) -> str:
    """Lifecycle rolled up from the member steps.

    Resolved only when every step is; one step a person marked a false positive
    does not resolve the work the others still describe. An unknown plan id
    reads as ``open``, because a step nobody has triaged is outstanding.

    ``false_positive`` needs every step to be one. It is reported separately
    from ``resolved`` because the two are different claims - the work was done
    against the work was never real - and a surface offering the four triage
    states has to be able to read back the state a person chose.
    """
    return roll_up_status(plan_status.get(step.plan_id, "open") for step in opportunity.steps)


def roll_up_status(step_states: Iterable[str]) -> str:
    """The rollup rule itself, over the member states alone.

    Split out so the transition writer, which holds stored rows rather than
    composed objects, applies the same rule instead of restating it.
    """
    states = set(step_states)
    if not states:
        return "open"
    if states == {"false_positive"}:
        return "false_positive"
    if states <= {"resolved", "false_positive"}:
        return "resolved"
    if states <= {"acknowledged", "resolved", "false_positive"}:
        return "acknowledged"
    return "open"


__all__ = [
    "EXCLUDED_TYPES",
    "STEP_ORDER",
    "OpportunityEvidence",
    "OpportunityStep",
    "RefactoringOpportunity",
    "compose_opportunities",
    "is_standalone_clone",
    "opportunity_kernel",
    "opportunity_public_id",
    "opportunity_status",
    "roll_up_status",
]
