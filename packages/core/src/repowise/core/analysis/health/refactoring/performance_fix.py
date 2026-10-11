"""Structured refactoring plans for safely describable performance fixes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.execution_graph import file_of_symbol

from ..perf.actionability import PerformanceFix
from ..perf.opportunities import PerformanceOpportunity
from ..perf.opportunity_rank import band
from .models import RefactoringSuggestion
from .preconditions import Applicability

# Per strategy: an optional step at the intervention, then one step per call
# site. Templates name the edit; the locations are the evidence's own, and
# ``{call}`` names the call each site repeats.
_STEPS: dict[str, tuple[str | None, str]] = {
    "batch_or_prefetch_io": (
        "Add a form of {sink} that takes every key at once",
        "Collect the keys before the loop and replace the per-key {call} with one batched call",
    ),
    "parallelize_independent_awaits": (
        None,
        "Run the independent {call}s concurrently, with a bound on how many at once",
    ),
    "replace_membership_collection": (None, "Build a set once before the loop and probe it"),
    "buffer_string_accumulation": (None, "Accumulate into a list and join once after the loop"),
    "shrink_lock_scope": ("Move the I/O in {sink} out of the locked section", ""),
    "push_reduction_into_query": (
        None,
        "Select one row per key in the query instead of reducing all rows here",
    ),
}
# When the fix names the construct it uses, the step says it instead.
_API_STEPS: dict[str, tuple[str | None, str]] = {
    "batch_or_prefetch_io": (
        None,
        "Collect the keys before the loop and make one call with {api} in place of the "
        "per-key {call}",
    ),
    "parallelize_independent_awaits": (
        None,
        "Run the independent {call}s concurrently, each still inside {api}",
    ),
}


@dataclass(frozen=True, slots=True)
class PerformancePlanPolicy:
    """Whether authoritative plans are generated, and at what confidence floor.

    Read from analyzer configuration and carried to the writer that persists
    them, because that writer sees the merged stored findings and this module
    has no view of configuration.
    """

    enabled: bool = True
    min_confidence: str | None = None


def fix_steps(
    strategy: str,
    safety: str,
    intervention: str | None,
    sink: str | None,
    locations: list[dict[str, object]],
    api: str | None = None,
) -> list[dict[str, object]]:
    """Ordered edits for one plan; mechanical only when the strategy is proven.

    *sink* is the sink every site shares, or ``None``: a bulk form added to one
    member's sink would leave the other sites' calls per-key.
    """
    intro, per_site = (_API_STEPS if api else _STEPS).get(strategy, (None, ""))
    applicability: Applicability = "mechanical" if safety == "proven" else "judgment"
    steps: list[dict[str, object]] = []
    target = intervention or sink
    # With nothing to name, a site step says the whole edit; a strategy with no
    # site step keeps its intro whatever it can name.
    if intro and (target or not per_site):
        steps.append(
            {
                "action": intro.format(sink=(target or "the per-key call").rsplit("::", 1)[-1]),
                "symbol": target,
                "file_path": file_of_symbol(target) if target else None,
                "line": None,
                "applicability": "judgment",
            }
        )
    if per_site:
        steps.extend(
            {
                "action": per_site.format(api=api, call=_call_phrase(location.get("call"))),
                "symbol": location.get("function_name"),
                "file_path": location.get("file_path"),
                "line": location.get("line_start"),
                **({"loop_line": location["loop_line"]} if location.get("loop_line") else {}),
                "applicability": applicability,
            }
            for location in locations
        )
    return [{"order": index, **step} for index, step in enumerate(steps, 1)]


def _call_phrase(call: object) -> str:
    return f"{call} call" if call else "call"


_EFFORT_BANDS = ((1, "S"), (3, "M"), (8, "L"))


def edit_effort(sites: int) -> str:
    """Effort from how many places change, not the size of the file they sit in."""
    return band(sites, _EFFORT_BANDS, "XL")


def performance_fix_suggestions(
    opportunities: Iterable[PerformanceOpportunity],
    *,
    min_confidence: str | None = None,
) -> list[RefactoringSuggestion]:
    """Convert opportunities with a safe strategy into one closed plan type.

    This is intentionally a repo-level service, not a per-file detector: one
    cross-function cause can address observations in many caller files.
    """
    confidence_order = {"low": 0, "medium": 1, "high": 2}
    floor = confidence_order.get((min_confidence or "").lower(), 0)
    out: list[RefactoringSuggestion] = []
    for opportunity in opportunities:
        if opportunity.execution_context == "test":
            continue
        fix = opportunity.fix
        confidence = "high" if fix and fix.safety == "proven" else "medium"
        if fix is None or not opportunity.evidence or confidence_order[confidence] < floor:
            continue
        out.append(_suggestion(opportunity, fix, confidence))
    return out


def _intervention(opportunity: PerformanceOpportunity, fix: PerformanceFix) -> str | None:
    """Where the edit lands. A lock fix edits the lock owner, the first node of
    its proven path, never the shared sink downstream of it."""
    anchor = opportunity.evidence[0]
    if fix.strategy == "shrink_lock_scope" and anchor.get("path"):
        return anchor["path"][0]
    return opportunity.intervention_symbol


def _site_locations(evidence: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One location per call site and loop, whatever number of markers observed it.

    ``io_in_loop`` and ``nested_loop_with_io`` both fire on one call; that is one
    edit, not two.
    """
    sites: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for item in evidence:
        key = (item["file_path"], item.get("line_start"), item.get("loop_line"))
        site = sites.setdefault(
            key,
            {
                "file_path": item["file_path"],
                "function_name": item.get("function_name"),
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
                **({"loop_line": item["loop_line"]} if item.get("loop_line") else {}),
            },
        )
        if "call" not in site and (call := _site_call(item)):
            site["call"] = call
    return list(sites.values())


def _site_call(item: dict[str, Any]) -> str | None:
    """The call the site repeats: the detector's text, else the helper hop.

    A cross-function path starts at the loop's own function (``path[0]``), so
    ``path[1]`` is the helper the loop calls at this site.
    """
    path = item.get("path") or ()
    hop = path[1] if len(path) > 1 and isinstance(path[1], str) else None
    return item.get("sink_call") or (hop.rsplit("::", 1)[-1] if hop else None)


def _shared_sink(opportunity: PerformanceOpportunity) -> str | None:
    """The sink every site reaches, or ``None`` when a site has none or they differ.

    Evidence is capped; past the cap only the members' resolved sinks are known,
    so a pathless member beyond it goes unseen (ceiling: carry a has-path count).
    """
    sinks = {item["path"][-1] if item.get("path") else None for item in opportunity.evidence}
    sink = sinks.pop() if len(sinks) == 1 else None
    if opportunity.evidence_truncated and sink != opportunity.terminal_sink:
        return None
    return sink


def _suggestion(
    opportunity: PerformanceOpportunity, fix: PerformanceFix, confidence: str
) -> RefactoringSuggestion:
    anchor = opportunity.evidence[0]
    intervention = _intervention(opportunity, fix)
    target_file = file_of_symbol(intervention) if intervention else anchor["file_path"]
    in_anchor_file = target_file == anchor["file_path"]
    locations = _site_locations(opportunity.evidence)
    # A bulk form belongs on the per-key callee: the shared helper, else the one
    # sink the loop reaches. The loop's own function is where the keys are
    # collected, never what gains the bulk form.
    batched_on_helper = opportunity.intervention_kind == "shared_helper"
    steps = fix_steps(
        fix.strategy,
        fix.safety,
        intervention if fix.strategy != "batch_or_prefetch_io" or batched_on_helper else None,
        _shared_sink(opportunity),
        locations,
        fix.api,
    )
    mechanical = sum(step["applicability"] == "mechanical" for step in steps)
    return RefactoringSuggestion(
        refactoring_type="performance_fix",
        file_path=target_file,
        target_symbol=intervention or str(anchor.get("function_name") or target_file),
        line_start=anchor.get("line_start") if in_anchor_file else None,
        line_end=anchor.get("line_end") if in_anchor_file else None,
        plan={
            "opportunity_id": opportunity.opportunity_id,
            "strategy": fix.strategy,
            "safety": fix.safety,
            "api": fix.api,
            "intervention_symbol": intervention,
            "affected_locations": locations,
            "affected_locations_total": opportunity.affected_call_sites_total,
            "paths": [item["path"] for item in opportunity.evidence if item.get("path")],
            "paths_total": opportunity.observations_total,
            "evidence_truncated": opportunity.evidence_truncated,
            "steps": steps,
            "mechanical_steps": mechanical,
            "judgment_steps": len(steps) - mechanical,
        },
        evidence={
            "biomarker_type": opportunity.biomarker_type,
            "biomarker_types": list(opportunity.biomarker_types),
            "boundary_kind": opportunity.boundary_kind,
            "execution_context": opportunity.execution_context,
            "provenance": opportunity.provenance,
            "reliable_entry_reachability": opportunity.reliable_entry_reachability,
            "rank_score": opportunity.rank_score,
            "rank_factors": opportunity.rank_factors,
            "rationale": fix.rationale,
            "observations_total": opportunity.observations_total,
            "affected_files_total": opportunity.affected_files_total,
        },
        # Performance is a separate score dimension and its findings
        # intentionally carry zero defect-health impact.
        impact_delta=0.0,
        effort_bucket=edit_effort(
            opportunity.affected_call_sites_total + (len(steps) > len(locations))
        ),
        blast_radius={
            "files": sorted({item["file_path"] for item in opportunity.evidence}),
            "file_count": opportunity.affected_files_total,
            "call_sites": opportunity.affected_call_sites_total,
        },
        confidence=confidence,
        source_biomarker=opportunity.biomarker_type,
    )


__all__ = ["PerformancePlanPolicy", "edit_effort", "fix_steps", "performance_fix_suggestions"]
