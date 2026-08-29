"""Evidence to actionability: whether a group supports naming one safe change.

Proven repetition is not proof that removing it is valid, and this module is
where that distinction is enforced. Every gate here returns ``None`` rather than
a weaker plan when it cannot prove its precondition, because a wrong plan costs
more than a missing one.

The confidence produced here is *evidence* confidence: how reliably the call
path was resolved. It is not a claim that the change is safe; that is carried
separately by :attr:`PerformanceFix.safety`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

FixSafety = Literal["proven", "advisory"]
OpportunityConfidence = Literal["high", "medium", "low"]
FixStrategy = Literal[
    "parallelize_independent_awaits",
    "replace_membership_collection",
    "buffer_string_accumulation",
    "hoist_loop_invariant_resource",
    "batch_or_prefetch_io",
    "shrink_lock_scope",
]


@dataclass(frozen=True, slots=True)
class PerformanceFix:
    strategy: FixStrategy
    safety: FixSafety
    rationale: str

    def as_dict(self) -> dict[str, str]:
        return {"strategy": self.strategy, "safety": self.safety, "rationale": self.rationale}


def provenance_confidence(provenance: str) -> OpportunityConfidence:
    """Product confidence label owned beside the provenance ranking policy."""
    if provenance in {"call-site", "direct"}:
        return "high"
    if provenance == "reliable-edge":
        return "medium"
    return "low"


def fix_for(
    marker: str,
    markers: tuple[str, ...],
    boundary: str | None,
    details: list[dict[str, Any]],
    *,
    cross_function: bool,
    shared_suffix: tuple[str, ...],
) -> PerformanceFix | None:
    """The one strategy this group's evidence supports, or nothing.

    Gate order is load-bearing: the marker-specific proofs run before the
    generic I/O batching fallback, so a group that qualifies for a proven
    transformation is never downgraded to an advisory one.
    """
    if marker == "serial_await_in_loop" and all(d.get("dataflow_verified") for d in details):
        return PerformanceFix(
            "parallelize_independent_awaits",
            "proven",
            "Dataflow proves that every observed loop carries no cross-iteration dependence.",
        )
    if marker == "membership_test_against_list_in_loop":
        return PerformanceFix(
            "replace_membership_collection",
            "advisory",
            "The collection is proven list-backed; element hashability and ordering/identity use still require validation.",
        )
    if marker == "string_concat_in_loop":
        return PerformanceFix(
            "buffer_string_accumulation",
            "advisory",
            "Repeated string accumulation is proven; intermediate accumulator observations still require validation.",
        )
    if set(markers) <= {"io_in_loop", "nested_loop_with_io"} and boundary in {
        "db",
        "network",
    }:
        if cross_function and len(shared_suffix) < 2:
            # A generic terminal resource/API shared by otherwise unrelated
            # callers (for example ``get_session``) is evidence of repeated
            # cost, but not proof that editing that sink is one coherent
            # intervention. Keep the opportunity visible without claiming a
            # batch plan.
            return None
        return PerformanceFix(
            "batch_or_prefetch_io",
            "advisory",
            "The shared I/O sink is proven; no concrete batch API or result-equivalence proof is available.",
        )
    if marker == "blocking_io_under_lock":
        path_starts = {
            path[0] for detail in details if (path := detail.get("path")) and isinstance(path, list)
        }
        if cross_function and len(path_starts) != 1:
            return None
        return PerformanceFix(
            "shrink_lock_scope",
            "advisory",
            "I/O under the lock is proven, but shared-state ordering must be validated before moving it.",
        )
    if marker == "resource_construction_in_loop" and all(
        d.get("resource_invariant") is True for d in details
    ):
        return PerformanceFix(
            "hoist_loop_invariant_resource",
            "proven",
            "Dataflow proves construction arguments and lifetime are loop invariant.",
        )
    return None


__all__ = [
    "FixSafety",
    "FixStrategy",
    "OpportunityConfidence",
    "PerformanceFix",
    "fix_for",
    "provenance_confidence",
]
