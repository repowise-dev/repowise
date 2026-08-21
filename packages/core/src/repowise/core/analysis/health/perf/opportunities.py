"""Deterministic causal read model over raw performance findings.

Raw health findings remain the line-level source of truth.  This module folds
them once into bounded, ranked opportunities so repeated caller paths to one
sink lead an agent to one intervention.  It is deliberately persistence-
agnostic: analyzer dataclasses, ORM rows, and lightweight SQL rows are accepted
through the same attribute adapter.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from math import log2
from typing import Any, Literal

from repowise.core.test_paths import is_test_related_path

ExecutionContext = Literal["production", "tooling", "test"]
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

_TOOLING_PARTS = frozenset(
    {".github", "benchmarks", "build", "devtools", "scripts", "tooling", "tools"}
)
_BOUNDARY_POINTS = {"subprocess": 5, "network": 4, "db": 4, "lock": 3, "filesystem": 2}
_MULTIPLIER_POINTS = {
    "nested_loop_with_io": 6,
    "nested_loop_quadratic": 6,
    "blocking_io_under_lock": 5,
    "io_in_loop": 4,
    "serial_await_in_loop": 4,
    "resource_construction_in_loop": 4,
    "membership_test_against_list_in_loop": 3,
    "string_concat_in_loop": 3,
    "lock_in_loop": 2,
}
_CONTEXT_POINTS = {"production": 3, "tooling": 2, "test": 1}
_PROVENANCE_POINTS = {"call-site": 3, "direct": 3, "reliable-edge": 2, "name-fallback": 0}


@dataclass(frozen=True, slots=True)
class PerformanceFix:
    strategy: FixStrategy
    safety: FixSafety
    rationale: str

    def as_dict(self) -> dict[str, str]:
        return {"strategy": self.strategy, "safety": self.safety, "rationale": self.rationale}


@dataclass(frozen=True, slots=True)
class PerformanceOpportunity:
    opportunity_id: str
    biomarker_type: str
    biomarker_types: tuple[str, ...]
    boundary_kind: str | None
    execution_context: ExecutionContext
    terminal_sink: str | None
    shared_path_suffix: tuple[str, ...]
    intervention_symbol: str | None
    affected_call_sites_total: int
    affected_files_total: int
    observations_total: int
    evidence: tuple[dict[str, Any], ...]
    evidence_truncated: bool
    reliable_entry_reachability: bool | None
    provenance: str
    confidence: OpportunityConfidence
    rank_score: int
    rank_factors: dict[str, int]
    fix: PerformanceFix | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "biomarker_type": self.biomarker_type,
            "biomarker_types": list(self.biomarker_types),
            "boundary_kind": self.boundary_kind,
            "execution_context": self.execution_context,
            "terminal_sink": self.terminal_sink,
            "shared_path_suffix": list(self.shared_path_suffix),
            "intervention_symbol": self.intervention_symbol,
            "affected_call_sites_total": self.affected_call_sites_total,
            "affected_files_total": self.affected_files_total,
            "observations_total": self.observations_total,
            "evidence": list(self.evidence),
            "evidence_truncated": self.evidence_truncated,
            "reliable_entry_reachability": self.reliable_entry_reachability,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "rank_score": self.rank_score,
            "rank_factors": dict(self.rank_factors),
            "fix": self.fix.as_dict() if self.fix else None,
        }


def _attr(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _details(row: Any) -> dict[str, Any]:
    value = _attr(row, "details", None)
    if isinstance(value, dict):
        return value
    raw = _attr(row, "details_json", None)
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def execution_context(file_path: str) -> ExecutionContext:
    if is_test_related_path(file_path):
        return "test"
    parts = {part.lower() for part in file_path.replace("\\", "/").split("/")}
    if parts & _TOOLING_PARTS or "/cli/" in f"/{file_path.lower().replace(chr(92), '/')}/":
        return "tooling"
    return "production"


def _stable_id(key: tuple[Any, ...]) -> str:
    payload = json.dumps(key, separators=(",", ":"), ensure_ascii=True)
    return "perf_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _cost_shape(marker: str) -> str:
    """Compatibility family for observations that may share one intervention."""
    if marker in {"io_in_loop", "nested_loop_with_io"}:
        return "repeated_io"
    return marker


def _causal_key(row: Any) -> tuple[Any, ...]:
    details = _details(row)
    marker = str(_attr(row, "biomarker_type", ""))
    file_path = str(_attr(row, "file_path", ""))
    context = execution_context(file_path)
    path = tuple(str(node) for node in details.get("path", ()) if isinstance(node, str))
    boundary = details.get("boundary_kind") or None
    if details.get("cross_function") and path:
        # Membership is immutable under caller churn: a new caller changes the
        # evidence and common suffix, never the identity of the shared cause.
        return ("cross-function", context, _cost_shape(marker), boundary, path[-1])
    return (
        "local",
        context,
        marker,
        boundary,
        file_path,
        _attr(row, "function_name", None),
        _attr(row, "line_start", None),
    )


def opportunity_id_for_finding(row: Any) -> str:
    return _stable_id(_causal_key(row))


def link_performance_findings(findings: list[Any]) -> None:
    """Attach the causal id to analyzer findings before they are persisted."""
    for finding in findings:
        if _attr(finding, "dimension", None) != "performance":
            continue
        details = _details(finding)
        details["opportunity_id"] = opportunity_id_for_finding(finding)


def _shared_suffix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    if not paths:
        return ()
    common: list[str] = []
    for nodes in zip(*(reversed(path) for path in paths), strict=False):
        if len(set(nodes)) != 1:
            break
        common.append(nodes[0])
    return tuple(reversed(common))


def _evidence(row: Any, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": str(_attr(row, "id", "") or ""),
        "file_path": str(_attr(row, "file_path", "")),
        "biomarker_type": str(_attr(row, "biomarker_type", "") or ""),
        "function_name": _attr(row, "function_name", None),
        "line_start": _attr(row, "line_start", None),
        "line_end": _attr(row, "line_end", None),
        "reason": str(_attr(row, "reason", "") or ""),
        "path": list(details.get("path", ())),
        "provenance": details.get("resolution_basis", "direct"),
    }


def provenance_confidence(provenance: str) -> OpportunityConfidence:
    """Product confidence label owned beside the provenance ranking policy."""
    if provenance in {"call-site", "direct"}:
        return "high"
    if provenance == "reliable-edge":
        return "medium"
    return "low"


def _fix_for(
    marker: str,
    markers: tuple[str, ...],
    boundary: str | None,
    details: list[dict[str, Any]],
    *,
    cross_function: bool,
    shared_suffix: tuple[str, ...],
) -> PerformanceFix | None:
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


def build_performance_opportunities(
    findings: list[Any], *, evidence_limit: int = 8
) -> list[PerformanceOpportunity]:
    """Group and rank performance rows in one deterministic pass."""
    groups: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
    for row in findings:
        if _attr(row, "dimension", None) == "performance":
            groups[_causal_key(row)].append(row)

    opportunities: list[PerformanceOpportunity] = []
    cap = max(0, evidence_limit)
    for key, rows in groups.items():
        rows.sort(
            key=lambda row: (
                str(_attr(row, "file_path", "")),
                _attr(row, "line_start", None) or 0,
                str(_attr(row, "function_name", "")),
            )
        )
        detail_rows = [_details(row) for row in rows]
        paths = [
            tuple(str(node) for node in details.get("path", ()) if isinstance(node, str))
            for details in detail_rows
            if details.get("path")
        ]
        suffix = _shared_suffix(paths)
        markers = tuple(sorted({str(_attr(row, "biomarker_type", "")) for row in rows}))
        marker = min(markers, key=lambda value: (-_MULTIPLIER_POINTS.get(value, 1), value))
        boundary = detail_rows[0].get("boundary_kind") or None
        context = execution_context(str(_attr(rows[0], "file_path", "")))
        sites = {
            (
                str(_attr(row, "file_path", "")),
                _attr(row, "line_start", None),
                _attr(row, "function_name", None),
            )
            for row in rows
        }
        files = {site[0] for site in sites}
        reach_values = {d.get("reliable_entry_reachability") for d in detail_rows}
        reachable: bool | None = (
            True if True in reach_values else (False if reach_values == {False} else None)
        )
        provenances = {str(d.get("resolution_basis", "direct")) for d in detail_rows}
        provenance = min(provenances, key=lambda value: (_PROVENANCE_POINTS.get(value, 0), value))
        factors = {
            "multiplier_shape": _MULTIPLIER_POINTS.get(marker, 1),
            "boundary_kind": _BOUNDARY_POINTS.get(boundary or "", 0),
            "execution_context": _CONTEXT_POINTS[context],
            "entry_reachability": 3 if reachable is True else 0,
            "affected_call_sites": min(8, int(log2(len(sites) + 1) * 2)),
            "provenance": _PROVENANCE_POINTS.get(provenance, 0),
        }
        evidence = tuple(
            _evidence(row, details)
            for row, details in zip(rows[:cap], detail_rows[:cap], strict=True)
        )
        opportunities.append(
            PerformanceOpportunity(
                opportunity_id=_stable_id(key),
                biomarker_type=marker,
                biomarker_types=markers,
                boundary_kind=boundary,
                execution_context=context,
                terminal_sink=paths[0][-1] if paths else None,
                shared_path_suffix=suffix,
                intervention_symbol=suffix[0] if suffix else None,
                affected_call_sites_total=len(sites),
                affected_files_total=len(files),
                observations_total=len(rows),
                evidence=evidence,
                evidence_truncated=len(rows) > cap,
                reliable_entry_reachability=reachable,
                provenance=provenance,
                confidence=provenance_confidence(provenance),
                rank_score=sum(factors.values()),
                rank_factors=factors,
                fix=_fix_for(
                    marker,
                    markers,
                    boundary,
                    detail_rows,
                    cross_function=key[0] == "cross-function",
                    shared_suffix=suffix,
                ),
            )
        )
    opportunities.sort(
        key=lambda item: (
            -item.rank_score,
            -item.affected_call_sites_total,
            item.opportunity_id,
        )
    )
    return opportunities


__all__ = [
    "FixSafety",
    "FixStrategy",
    "PerformanceFix",
    "PerformanceOpportunity",
    "build_performance_opportunities",
    "execution_context",
    "link_performance_findings",
    "opportunity_id_for_finding",
    "provenance_confidence",
]
