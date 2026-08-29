"""Applicability facts for one refactoring step, and what they license.

Two questions, one owner. *What do we know* about a plan's surroundings, and
*is that enough to call the step mechanical* rather than a judgment call a
person has to make.

Both answers come only from facts the layer already records: the plan payload,
its evidence, its blast radius, and the detector's own confidence. Nothing here
queries a graph, reads source, or derives a signal a detector did not already
publish, so the classification is identical whether it runs beside the analyzer
or over rows read back out of storage months later.

Unknown is a first-class answer. ``mechanical`` means every proof obligation the
step needs is *held*, not merely *unrefuted*: a fact we cannot see refuses the
promotion, exactly as the Extract Method gate refuses a statement kind it cannot
classify. That keeps the layer's contract - no suggestion, never a wrong one -
true of the classification as well as of the plans.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .models import RefactoringSuggestion
from .recommendations import affected_files, blast_size

Applicability = Literal["mechanical", "judgment"]

# Reason codes are a closed vocabulary so a surface can label them and a test
# can pin them. They read as the answer to "why is this not mechanical?", or,
# for the two mechanical codes, "what was proved?".
MECHANICAL_REASONS = ("dataflow_proved_local_extraction",)
JUDGMENT_REASONS = (
    "build_constraints_unknown",
    "call_site_bindings_unproven",
    "changes_symbol_home",
    "detector_confidence_below_high",
    "inverts_imports_across_files",
    "no_named_target",
    "reshapes_class_surface",
    "rewrites_dependent_imports",
    "unclassified_refactoring_type",
)

# The categorical blast radius Extract Method publishes: extraction adds a
# private helper and changes no signature, so nothing outside the file moves.
_LOCAL_SCOPE = "local"


@dataclass(frozen=True, slots=True)
class StepApplicability:
    """What is known about a step, and whether that licenses automation."""

    classification: Applicability
    reasons: tuple[str, ...]
    facts: dict[str, Any]
    unknowns: tuple[str, ...]

    @property
    def mechanical(self) -> bool:
        return self.classification == "mechanical"

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "reasons": list(self.reasons),
            "facts": dict(self.facts),
            "unknowns": list(self.unknowns),
        }


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _named_groups(plan: dict[str, Any]) -> bool | None:
    """Whether every proposed group names the file it lands in.

    ``None`` when the plan proposes no groups at all: absence of groups is not
    evidence that the naming succeeded, and R1 made an unnameable group emit
    ``null`` rather than invent a filename.
    """
    groups = [group for group in (plan.get("groups") or []) if isinstance(group, dict)]
    if not groups:
        return None
    return all(group.get("suggested_file") for group in groups)


# Which facts each refactoring type can even have. A key absent here is not
# unknown, it is meaningless: asking whether an Extract Method needs a
# re-export shim has no answer, and reporting one as ``None`` would read as a
# measurement that failed. Only the keys a type can carry are published, so
# ``unknowns`` names facts that genuinely could not be established.
_BASE_FACT_KEYS = ("affected_file_count", "cross_file", "blast_size", "confidence")
_TYPE_FACT_KEYS: dict[str, tuple[str, ...]] = {
    "extract_method": ("local_scope",),
    "split_file": ("shim_required", "groups_named", "dependents", "framework_registration"),
    "extract_class": ("dependents", "framework_registration"),
    "move_method": ("callers", "framework_registration"),
    "break_cycle": ("framework_registration",),
    "extract_helper": ("co_change_count",),
}


def step_facts(suggestion: RefactoringSuggestion) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Applicability facts for *suggestion*, plus the names of the missing ones.

    A key is present with ``None`` when the fact is one this step's type does
    carry but this row does not, so a reader can tell "no dependents" from "we
    never counted". The ``unknowns`` tuple names exactly those keys.
    """
    plan = suggestion.plan or {}
    evidence = suggestion.evidence or {}
    blast = suggestion.blast_radius or {}
    files = affected_files(suggestion)

    available: dict[str, Any] = {
        "affected_file_count": len(files),
        "cross_file": len(files) > 1,
        "blast_size": blast_size(suggestion),
        "confidence": suggestion.confidence,
        "local_scope": blast.get("scope") == _LOCAL_SCOPE,
        "dependents": _int_or_none(blast.get("dependent_count", blast.get("dependents_count"))),
        "callers": _int_or_none(blast.get("callers")),
        "co_change_count": _int_or_none(evidence.get("co_change_count")),
        "shim_required": plan.get("shim_required") if "shim_required" in plan else None,
        "groups_named": _named_groups(plan),
        # No graph is in scope here, so whether a framework registers the symbols
        # a step moves is never known. It is reported rather than omitted because
        # it is the fact that keeps every symbol-moving step a judgment call.
        "framework_registration": None,
    }
    keys = _BASE_FACT_KEYS + _TYPE_FACT_KEYS.get(suggestion.refactoring_type, ())
    facts = {key: available[key] for key in keys}
    unknowns = tuple(sorted(key for key, value in facts.items() if value is None))
    return facts, unknowns


def _extract_method_reasons(suggestion: RefactoringSuggestion, facts: dict[str, Any]) -> list[str]:
    """A lifted span adds a private helper and moves no public symbol.

    R1's dataflow gate proves the span behaviour-preserving before it is ever
    offered, and the blast radius is categorically local, so the only thing left
    to check is that the detector itself was sure.
    """
    if not facts["local_scope"]:
        return ["changes_symbol_home"]
    if suggestion.confidence != "high":
        return ["detector_confidence_below_high"]
    return ["dataflow_proved_local_extraction"]


def _split_file_reasons(_suggestion: RefactoringSuggestion, facts: dict[str, Any]) -> list[str]:
    """No split is mechanical, and the same-package case is why.

    The promising case was Go: package members keep their qualified name
    whichever file declares them, so a same-package split rewrites no import
    and no registry keyed on the module path can notice. That argument covers
    import identity and nothing else. Which *file* a Go symbol sits in also
    decides whether it compiles at all - a ``_linux.go`` / ``_amd64.go``
    basename, or a ``//go:build`` line - and neither fact reaches this layer:
    the plan payload carries group membership and a shim flag, not the source.
    Moving a constrained symbol into an unconstrained file compiles it
    everywhere, which is a behaviour change presented as safe to automate.

    So the same-package case reports what it could not establish instead of
    claiming a proof. Restoring it needs a build-constraint fact from
    ingestion; see the plan's Backlog.
    """
    if facts["shim_required"] is not False:
        return ["rewrites_dependent_imports", "changes_symbol_home"]
    if facts["groups_named"] is not True:
        return ["no_named_target"]
    return ["build_constraints_unknown"]


_JUDGMENT_BY_TYPE: dict[str, tuple[str, ...]] = {
    # A shared helper replaces N call sites whose bindings the detector compares
    # by token shape, never by resolved meaning.
    "extract_helper": ("call_site_bindings_unproven",),
    "move_method": ("changes_symbol_home",),
    "extract_class": ("reshapes_class_surface", "changes_symbol_home"),
    "break_cycle": ("inverts_imports_across_files",),
}


def classify_step(suggestion: RefactoringSuggestion) -> StepApplicability:
    """Whether *suggestion* is safe to automate, and the facts behind that."""
    facts, unknowns = step_facts(suggestion)
    kind = suggestion.refactoring_type
    if kind == "extract_method":
        reasons = _extract_method_reasons(suggestion, facts)
    elif kind == "split_file":
        reasons = _split_file_reasons(suggestion, facts)
    else:
        reasons = list(_JUDGMENT_BY_TYPE.get(kind, ("unclassified_refactoring_type",)))
    # ``all`` over nothing is True, so a future branch that returned no reason
    # would promote silently. Mechanical has to be positively argued.
    mechanical = bool(reasons) and all(reason in MECHANICAL_REASONS for reason in reasons)
    return StepApplicability(
        classification="mechanical" if mechanical else "judgment",
        reasons=tuple(reasons),
        facts=facts,
        unknowns=unknowns,
    )


__all__ = [
    "JUDGMENT_REASONS",
    "MECHANICAL_REASONS",
    "Applicability",
    "StepApplicability",
    "classify_step",
    "step_facts",
]
