"""Whether a group's evidence supports naming one safe change.

Proven repetition is not proof that removing it is valid. Every gate declines
rather than offering a weaker plan, because a wrong plan costs more than a
missing one, and every refusal names the fact that would settle it.

Three separate questions live here and must not collapse into one label:

* evidence confidence, :func:`provenance_confidence`, asks how reliably the
  call path was resolved;
* fix safety, :attr:`PerformanceFix.safety`, asks how strongly the specific
  transformation is proven;
* actionability, :func:`actionability`, asks what to do with the group now, and
  demotes a proven strategy whose evidence is weak.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

FixSafety = Literal["proven", "advisory"]
OpportunityConfidence = Literal["high", "medium", "low"]
ActionabilityState = Literal["plan_ready", "advisory", "investigate"]
FixStrategy = Literal[
    "parallelize_independent_awaits",
    "replace_membership_collection",
    "buffer_string_accumulation",
    "hoist_loop_invariant_resource",
    "batch_or_prefetch_io",
    "shrink_lock_scope",
]

BATCHABLE_MARKERS = frozenset({"io_in_loop", "nested_loop_with_io"})
BATCHABLE_BOUNDARIES = frozenset({"db", "network"})


@dataclass(frozen=True, slots=True)
class PerformanceFix:
    strategy: FixStrategy
    safety: FixSafety
    rationale: str

    def as_dict(self) -> dict[str, str]:
        return {"strategy": self.strategy, "safety": self.safety, "rationale": self.rationale}


@dataclass(frozen=True, slots=True)
class FixAssessment:
    """The strategy this group supports, and what is still unproven.

    Prerequisites are stable machine tokens rather than prose: a caller renders
    them, and a new detector fact is expected to clear one by name. They are
    populated whether or not a fix was returned, because an offered advisory
    strategy has open questions too.
    """

    fix: PerformanceFix | None
    prerequisites: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Actionability:
    """The verdict the rest of the system acts on.

    It carries the fix as well as the label, because a demotion that moved only
    the label would leave the plan layer reading the undemoted strategy and
    stamping it with the confidence the demotion just withdrew.
    """

    state: ActionabilityState
    reason: str
    confidence: OpportunityConfidence
    prerequisites: tuple[str, ...]
    fix: PerformanceFix | None


def provenance_confidence(provenance: str) -> OpportunityConfidence:
    """Evidence confidence: how reliably the resolved call path holds.

    Only the first interprocedural hop carries a resolution basis. Deeper hops
    are unlabelled edges that already passed the graph's reliability filter, so
    this describes the labelled hop alone and is never presented as a verdict
    on the whole path.
    """
    if provenance in {"call-site", "direct"}:
        return "high"
    if provenance == "reliable-edge":
        return "medium"
    return "low"


def assess_fix(
    marker: str,
    markers: tuple[str, ...],
    boundary: str | None,
    details: list[dict[str, Any]],
    *,
    cross_function: bool,
) -> FixAssessment:
    """The one strategy this group's evidence supports, or the missing fact.

    Gate order is load-bearing: marker-specific proofs run before the generic
    batching fallback, so a group that qualifies for a proven transformation is
    never downgraded to an advisory one. That ordering is why this stays an
    explicit ladder rather than a lookup table; its branch count is the number
    of markers, and it is the one place a wrong answer ships a wrong edit.
    """
    if marker == "serial_await_in_loop":
        if not all(detail.get("dataflow_verified") for detail in details):
            return FixAssessment(None, ("loop_carried_dependence_proof",))
        return FixAssessment(
            PerformanceFix(
                "parallelize_independent_awaits",
                "proven",
                "Dataflow proves that every observed loop carries no cross-iteration dependence.",
            ),
            (),
        )
    if marker == "membership_test_against_list_in_loop":
        return FixAssessment(
            PerformanceFix(
                "replace_membership_collection",
                "advisory",
                "The collection is proven list-backed; element hashability and "
                "ordering/identity use still require validation.",
            ),
            ("element_hashability", "ordering_and_identity_use"),
        )
    if marker == "string_concat_in_loop":
        return FixAssessment(
            PerformanceFix(
                "buffer_string_accumulation",
                "advisory",
                "Repeated string accumulation is proven; intermediate accumulator "
                "observations still require validation.",
            ),
            ("accumulator_not_observed",),
        )
    if set(markers) <= BATCHABLE_MARKERS:
        if boundary not in BATCHABLE_BOUNDARIES:
            # Filesystem and subprocess repetition is real, but there is no
            # batch or prefetch operation to point the caller at.
            return FixAssessment(None, ("batch_operation_for_boundary",))
        return FixAssessment(
            PerformanceFix(
                "batch_or_prefetch_io",
                "advisory",
                "The shared I/O sink is proven; no concrete batch API or "
                "result-equivalence proof is available.",
            ),
            ("batch_api_contract", "result_equivalence"),
        )
    if marker == "blocking_io_under_lock":
        owners = {
            path[0] for detail in details if (path := detail.get("path")) and isinstance(path, list)
        }
        if cross_function and len(owners) != 1:
            return FixAssessment(None, ("single_lock_owner",))
        return FixAssessment(
            PerformanceFix(
                "shrink_lock_scope",
                "advisory",
                "I/O under the lock is proven, but shared-state ordering must be "
                "validated before moving it.",
            ),
            ("shared_state_ordering",),
        )
    if marker == "resource_construction_in_loop":
        if not all(detail.get("resource_invariant") is True for detail in details):
            return FixAssessment(None, ("loop_invariant_construction_proof",))
        return FixAssessment(
            PerformanceFix(
                "hoist_loop_invariant_resource",
                "proven",
                "Dataflow proves construction arguments and lifetime are loop invariant.",
            ),
            (),
        )
    return FixAssessment(None, ("supported_strategy_for_marker",))


def actionability(
    assessment: FixAssessment, evidence_confidence: OpportunityConfidence
) -> Actionability:
    """What to do with this group next, and why not more.

    Deliberately not a restatement of fix safety. A proven strategy resting on
    a call path we could not resolve reliably is still only advisory, and the
    demotion names the fact that would promote it. A group with no strategy is
    kept as investigation evidence rather than dropped.
    """
    fix = assessment.fix
    if fix is None:
        return Actionability(
            "investigate", "no_supported_strategy", "low", assessment.prerequisites, None
        )
    if evidence_confidence == "low":
        # A transformation proven against a path we could not resolve is not
        # proven against this code. The safety label moves with the verdict so
        # the plan layer cannot read the withdrawn one.
        return Actionability(
            "advisory",
            "low_evidence_confidence",
            "low",
            (*assessment.prerequisites, "reliable_call_path"),
            replace(fix, safety="advisory") if fix.safety == "proven" else fix,
        )
    if fix.safety == "proven":
        return Actionability(
            "plan_ready", "proven_strategy", "high", assessment.prerequisites, fix
        )
    return Actionability(
        "advisory", "strategy_requires_validation", "medium", assessment.prerequisites, fix
    )


__all__ = [
    "BATCHABLE_BOUNDARIES",
    "BATCHABLE_MARKERS",
    "Actionability",
    "ActionabilityState",
    "FixAssessment",
    "FixSafety",
    "FixStrategy",
    "OpportunityConfidence",
    "PerformanceFix",
    "actionability",
    "assess_fix",
    "provenance_confidence",
]
