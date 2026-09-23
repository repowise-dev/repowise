"""Structured refactoring plans for safely describable performance fixes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from repowise.core.analysis.execution_graph import file_of_symbol

from ..perf.actionability import PerformanceFix
from ..perf.opportunities import PerformanceOpportunity
from ..perf.opportunity_rank import band
from .models import RefactoringSuggestion
from .preconditions import Applicability

# Per strategy: an optional step at the intervention, then one step per call
# site. Templates name the edit; the locations are the evidence's own.
_STEPS: dict[str, tuple[str | None, str]] = {
    "batch_or_prefetch_io": (
        "Add a form of {sink} that takes every key at once",
        "Collect the keys before the loop and call the batched form once",
    ),
    "parallelize_independent_awaits": (
        None,
        "Run the independent awaits concurrently, with a bound on how many at once",
    ),
    "replace_membership_collection": (None, "Build a set once before the loop and probe it"),
    "buffer_string_accumulation": (None, "Accumulate into a list and join once after the loop"),
    "hoist_loop_invariant_resource": (None, "Construct the resource once before the loop"),
    "shrink_lock_scope": ("Move the I/O in {sink} out of the locked section", ""),
    "push_reduction_into_query": (
        None,
        "Select one row per key in the query instead of reducing all rows here",
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
) -> list[dict[str, object]]:
    """Ordered edits for one plan; mechanical only when the strategy is proven."""
    intro, per_site = _STEPS.get(strategy, (None, ""))
    applicability: Applicability = "mechanical" if safety == "proven" else "judgment"
    steps: list[dict[str, object]] = []
    if intro:
        target = intervention or sink
        steps.append(
            {
                "action": intro.format(sink=(target or "the shared sink").rsplit("::", 1)[-1]),
                "symbol": target,
                "file_path": file_of_symbol(target) if target else None,
                "line": None,
                "applicability": "judgment",
            }
        )
    if per_site:
        steps.extend(
            {
                "action": per_site,
                "symbol": location.get("function_name"),
                "file_path": location.get("file_path"),
                "line": location.get("line_start"),
                "applicability": applicability,
            }
            for location in locations
        )
    return [{"order": index, **step} for index, step in enumerate(steps, 1)]


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


def _suggestion(
    opportunity: PerformanceOpportunity, fix: PerformanceFix, confidence: str
) -> RefactoringSuggestion:
    anchor = opportunity.evidence[0]
    intervention = _intervention(opportunity, fix)
    target_file = file_of_symbol(intervention) if intervention else anchor["file_path"]
    in_anchor_file = target_file == anchor["file_path"]
    locations = [
        {
            "file_path": item["file_path"],
            "function_name": item.get("function_name"),
            "line_start": item.get("line_start"),
            "line_end": item.get("line_end"),
        }
        for item in opportunity.evidence
    ]
    steps = fix_steps(fix.strategy, fix.safety, intervention, opportunity.terminal_sink, locations)
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
