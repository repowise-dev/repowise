"""Structured refactoring plans for safely describable performance fixes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from repowise.core.analysis.execution_graph import file_of_symbol

from ..perf.opportunities import PerformanceOpportunity
from .models import RefactoringSuggestion
from .registry import effort_bucket


def performance_fix_suggestions(
    opportunities: Iterable[PerformanceOpportunity],
    *,
    nloc_by_file: Mapping[str, int] | None = None,
    min_confidence: str | None = None,
) -> list[RefactoringSuggestion]:
    """Convert opportunities with a safe strategy into one closed plan type.

    This is intentionally a repo-level service, not a per-file detector: one
    cross-function cause can address observations in many caller files.
    """
    nloc_by_file = nloc_by_file or {}
    confidence_order = {"low": 0, "medium": 1, "high": 2}
    floor = confidence_order.get((min_confidence or "").lower(), 0)
    out: list[RefactoringSuggestion] = []
    for opportunity in opportunities:
        fix = opportunity.fix
        if fix is None or not opportunity.evidence:
            continue
        confidence = "high" if fix.safety == "proven" else "medium"
        if confidence_order[confidence] < floor:
            continue
        anchor = opportunity.evidence[0]
        intervention = opportunity.intervention_symbol
        if fix.strategy == "shrink_lock_scope" and anchor.get("path"):
            # The lock owner is the first node in the proven lock-to-I/O path;
            # editing a shared downstream sink would not shorten its critical
            # section. Opportunity construction only emits this plan when all
            # grouped paths have the same owner.
            intervention = anchor["path"][0]
        target_file = file_of_symbol(intervention) if intervention else anchor["file_path"]
        locations = [
            {
                "file_path": item["file_path"],
                "function_name": item.get("function_name"),
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
            }
            for item in opportunity.evidence
        ]
        paths = [item["path"] for item in opportunity.evidence if item.get("path")]
        out.append(
            RefactoringSuggestion(
                refactoring_type="performance_fix",
                file_path=target_file,
                target_symbol=intervention or str(anchor.get("function_name") or target_file),
                line_start=(
                    anchor.get("line_start") if target_file == anchor["file_path"] else None
                ),
                line_end=(anchor.get("line_end") if target_file == anchor["file_path"] else None),
                plan={
                    "opportunity_id": opportunity.opportunity_id,
                    "strategy": fix.strategy,
                    "safety": fix.safety,
                    "intervention_symbol": intervention,
                    "affected_locations": locations,
                    "affected_locations_total": opportunity.affected_call_sites_total,
                    "paths": paths,
                    "paths_total": opportunity.observations_total,
                    "evidence_truncated": opportunity.evidence_truncated,
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
                effort_bucket=effort_bucket(int(nloc_by_file.get(target_file, 0))),
                blast_radius={
                    "files": sorted({item["file_path"] for item in opportunity.evidence}),
                    "file_count": opportunity.affected_files_total,
                    "call_sites": opportunity.affected_call_sites_total,
                },
                confidence=confidence,
                source_biomarker=opportunity.biomarker_type,
            )
        )
    return out


__all__ = ["performance_fix_suggestions"]
