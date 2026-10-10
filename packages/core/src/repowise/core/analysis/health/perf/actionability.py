"""Whether a group's evidence supports naming one safe change.

Proven repetition is not proof that removing it is valid. Every gate declines
rather than offering a weaker plan, because a wrong plan costs more than a
missing one, and every refusal names the fact that would settle it.

Three separate questions live here and must not collapse into one label:

* evidence confidence, :func:`provenance_confidence`, asks how reliably the
  call path was resolved;
* fix safety, :attr:`PerformanceFix.safety`, asks how strongly the specific
  transformation is proven, not whether the runtime can absorb it;
* actionability, :func:`actionability`, asks what to do with the group now, and
  demotes a proven strategy whose evidence is weak.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from ..worth import dormant
from .cold_paths import is_cold_path

FixSafety = Literal["proven", "advisory"]
OpportunityConfidence = Literal["high", "medium", "low"]
# ``expected``: the repetition is real and there is nothing to change, either
# by its nature or for a reason :func:`expected_reason` reads off the group
# (``gated_off``: a constant-false flag switches the function off;
# ``cold_path``: it runs once per deploy, boot or incident;
# ``bounded_loop``: the loop runs a fixed number of times).
ActionabilityState = Literal["plan_ready", "advisory", "investigate", "expected"]

#: Reasons :func:`expected_reason` gives that the default queue counts under
#: their own name, in precedence order; ``bounded_loop`` counts as ``expected``.
EXPECTED_REASONS: tuple[str, ...] = ("gated_off", "cold_path")

# Refusals that are facts about the code, not missing proofs: nothing to investigate.
_EXPECTED_REFUSALS = frozenset({"inherent_to_boundary", "loop_already_chunked"})
FixStrategy = Literal[
    "parallelize_independent_awaits",
    "replace_membership_collection",
    "buffer_string_accumulation",
    "batch_or_prefetch_io",
    "shrink_lock_scope",
    "push_reduction_into_query",
    "eager_load_relationship",
]

BATCHABLE_MARKERS = frozenset({"io_in_loop", "nested_loop_with_io"})
BATCHABLE_BOUNDARIES = frozenset({"db", "network"})

# Fanning out N awaits here spends a pool, rate limit or statement timeout.
CONCURRENCY_SENSITIVE_BOUNDARIES = frozenset({"db", "network"})

# I/O a lock usually exists to serialize: a file-backed store or log writes
# under its lock, and a launcher spawns its process once under it. Moving that
# I/O out of the lock reintroduces the race the lock prevents.
LOCK_SERIALIZED_BOUNDARIES = frozenset({"filesystem", "subprocess"})


@dataclass(frozen=True, slots=True)
class PerformanceFix:
    strategy: FixStrategy
    safety: FixSafety
    rationale: str
    # The concrete construct the edit uses (a bulk call, a bound), when one was found.
    api: str | None = None

    def as_dict(self) -> dict[str, str]:
        out = {"strategy": self.strategy, "safety": self.safety, "rationale": self.rationale}
        if self.api:
            out["api"] = self.api
        return out


def _shared(details: list[dict[str, Any]], key: str) -> Any:
    """The one value every detail carries under *key*, else ``None``."""
    values = {detail.get(key) for detail in details}
    return values.pop() if len(values) == 1 else None


@dataclass(frozen=True, slots=True)
class FixAssessment:
    """The strategy this group supports, and what is still unproven.

    Prerequisites are stable machine tokens rather than prose: a caller renders
    them, and a new detector fact is expected to clear one by name. They are
    populated whether or not a fix was returned, because an offered advisory
    strategy has open questions too. ``refusal`` names why no strategy was
    offered when the reason is a fact about the code, not a missing proof.
    """

    fix: PerformanceFix | None
    prerequisites: tuple[str, ...]
    refusal: str = "no_supported_strategy"


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


# Markers whose FixAssessment depends on nothing but the marker itself: looked up
# once, at the point in the gate order the first of them used to sit.
_CONSTANT_FIXES: dict[str, FixAssessment] = {
    "membership_test_against_list_in_loop": FixAssessment(
        PerformanceFix(
            "replace_membership_collection",
            "advisory",
            "The collection is proven list-backed; element hashability and "
            "ordering/identity use still require validation.",
        ),
        ("element_hashability", "ordering_and_identity_use"),
    ),
    "string_concat_in_loop": FixAssessment(
        PerformanceFix(
            "buffer_string_accumulation",
            "advisory",
            "Repeated string accumulation is proven; intermediate accumulator "
            "observations still require validation.",
        ),
        ("accumulator_not_observed",),
    ),
    "unbounded_read_reduced_in_memory": FixAssessment(
        PerformanceFix(
            "push_reduction_into_query",
            "advisory",
            "The read is proven unbounded and the per-key selection proven "
            "Python-side; whether the query layer can express that "
            "selection (DISTINCT ON / a window function / a view) is not.",
        ),
        ("query_supports_group_selection",),
    ),
    "lazy_load_in_loop": FixAssessment(
        PerformanceFix(
            "eager_load_relationship",
            "advisory",
            "The relationship is declared lazy and the query that produced the "
            "rows does not load it; whether every iteration reaches the access, "
            "and whether another layer loads it first, is not proven.",
        ),
        ("relationship_not_loaded_elsewhere",),
    ),
    # Hoisting needs per-argument dataflow plus a guard against attribute mutation
    # (``Client(token=self.token)``); neither exists, so no strategy.
    "resource_construction_in_loop": FixAssessment(None, ("loop_invariant_construction_proof",)),
}


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
        if boundary in CONCURRENCY_SENSITIVE_BOUNDARIES:
            # A small fixed trip count is not a bound: that is what a retry loop
            # looks like, and retries must stay sequential.
            bound = _shared(details, "concurrency_bound")
            if bound:
                return FixAssessment(
                    PerformanceFix(
                        "parallelize_independent_awaits",
                        "proven",
                        f"Iterations are independent and each await already runs under {bound}.",
                        bound,
                    ),
                    (),
                )
            # Independence is proven; nothing here bounds the fan-out.
            return FixAssessment(
                PerformanceFix(
                    "parallelize_independent_awaits",
                    "advisory",
                    "Iteration independence is proven; the concurrency the fan-out "
                    "would create is not bounded by anything this group can see.",
                ),
                ("bounded_concurrency",),
            )
        return FixAssessment(
            PerformanceFix(
                "parallelize_independent_awaits",
                "proven",
                "Dataflow proves that every observed loop carries no cross-iteration dependence.",
            ),
            (),
        )
    if marker in _CONSTANT_FIXES:
        return _CONSTANT_FIXES[marker]
    if set(markers) <= BATCHABLE_MARKERS:
        if details and all(detail.get("chunked_iteration") for detail in details):
            # The loop is already the batch; "batch this" repeats advice taken.
            return FixAssessment(None, (), refusal="loop_already_chunked")
        if boundary not in BATCHABLE_BOUNDARIES:
            # Filesystem and subprocess repetition is real, but there is no
            # batch or prefetch operation to point the caller at.
            return FixAssessment(None, (), refusal="inherent_to_boundary")
        form = _shared(details, "batch_form")
        if form:
            if _shared(details, "batch_equivalent") is True:
                return FixAssessment(
                    PerformanceFix(
                        "batch_or_prefetch_io",
                        "proven",
                        f"Every call filters on the loop's own key, so {form} covers the same "
                        "rows, and nothing else in the loop can observe the difference.",
                        form,
                    ),
                    (),
                )
            return FixAssessment(
                PerformanceFix(
                    "batch_or_prefetch_io",
                    "advisory",
                    f"Every call filters on the loop's own key, so {form} is the bulk form; "
                    "the per-key call limits, orders or shares the loop with other I/O.",
                    form,
                ),
                ("result_equivalence",),
            )
        if details and all(detail.get("loop_key_unused") for detail in details):
            # No element or index of the loop reaches the call: a retry,
            # fallback or partial-write loop, with no set of keys to batch.
            return FixAssessment(None, ("per_key_call",))
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
        if boundary in LOCK_SERIALIZED_BOUNDARIES:
            return FixAssessment(None, ("io_not_guarded_by_lock",), refusal="lock_serializes_io")
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
    return FixAssessment(None, ("supported_strategy_for_marker",))


def expected_reason(members: Sequence[Any], magnitude: str | None = None) -> str | None:
    """Why a group needs no change whatever its strategy, or ``None``.

    ``gated_off``: every member sits in a function a constant-false flag in
    its own file switches off. ``cold_path``: every loop owner is named for a
    migration, startup, shutdown or crash recovery (:mod:`.cold_paths`), so it
    runs once per deploy, boot or incident. ``bounded_loop``: the group's loop
    *magnitude* is proven bounded, so its cost does not grow. Reasons are
    checked in precedence order.
    """
    if not members:
        return None
    if all(dormant(facts.details) for facts in members):
        return "gated_off"
    if all(is_cold_path(facts.file_path, facts.function_name) for facts in members):
        return "cold_path"
    return "bounded_loop" if magnitude == "bounded" else None


def actionability(
    assessment: FixAssessment,
    evidence_confidence: OpportunityConfidence,
    *,
    expected_reason: str | None = None,
) -> Actionability:
    """What to do with this group next, and why not more.

    Deliberately not a restatement of fix safety. A proven strategy resting on
    a call path we could not resolve reliably is still only advisory, and the
    demotion names the fact that would promote it. A group with no strategy is
    kept as investigation evidence rather than dropped. A group with an
    *expected_reason* (:func:`expected_reason`) has nothing to change, so it is
    ``expected`` under that reason and carries no fix.
    """
    # With or without a strategy: a group with none would otherwise be
    # ``investigate``, and there is nothing to investigate either.
    if expected_reason is not None:
        return Actionability("expected", expected_reason, "low", (), None)
    fix = assessment.fix
    if fix is None:
        state: ActionabilityState = (
            "expected" if assessment.refusal in _EXPECTED_REFUSALS else "investigate"
        )
        return Actionability(state, assessment.refusal, "low", assessment.prerequisites, None)
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
    "LOCK_SERIALIZED_BOUNDARIES",
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
