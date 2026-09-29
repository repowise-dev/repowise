"""Row serializers for get_health, with the public ids and perf rank they carry."""

from __future__ import annotations

import json
from typing import Any

from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.analysis.health.grading import TARGET_SCORE
from repowise.core.analysis.health.perf.opportunity_rank import observation_rank
from repowise.core.analysis.health.refactoring.recommendations import (
    Recommendation,
    build_recommendations,
)
from repowise.core.persistence.models import (
    HealthFileMetric,
    HealthFinding,
)
from repowise.server.mcp_server._references import (
    path_identity,
    refactoring_plan_id,
    stable_entity_id,
)


def _perf_rank(biomarker_type: str | None, details: Any) -> int:
    """Order-of-magnitude ordering key for one ``performance`` finding.

    Performance findings all carry ``health_impact: 0``, so they need their own
    key. The weights live with the opportunity ranking, so a finding and its
    opportunity cannot disagree. Never blended into ``score``.
    """
    if not isinstance(details, dict):
        details = {}
    return observation_rank(
        biomarker_type, details.get("boundary_kind"), bool(details.get("cross_function"))
    )


def _rank_emitted(rows: list[Any]) -> list[Any]:
    """Break the ``health_impact`` ties that the performance dimension is made of.

    Re-sorts only within each impact tier, so the defect ordering is untouched.
    ``file_path`` is the final key, so the order is total and reproducible.
    Rows read without ``details_json`` rank on the marker alone.
    """
    if not rows:
        return rows

    def key(r: Any) -> tuple[float, int, str]:
        impact = float(getattr(r, "health_impact", 0.0) or 0.0)
        dimension = getattr(r, "dimension", None) or "defect"
        if dimension != "performance":
            return (-impact, 0, getattr(r, "file_path", "") or "")
        raw = getattr(r, "details_json", None)
        try:
            details = json.loads(raw) if raw else {}
        except Exception:
            details = {}
        return (
            -impact,
            -_perf_rank(getattr(r, "biomarker_type", None), details),
            getattr(r, "file_path", "") or "",
        )

    return sorted(rows, key=key)


def _health_finding_id(f: Any, repository: str) -> str:
    """The finding's public id: the stored one, else the same kernel recomputed.

    Storage row ids change on every analysis, so this is the id evidence
    carries and ``finding_id`` resolves.
    """
    stored = getattr(f, "public_id", None)
    return stored if isinstance(stored, str) and stored else finding_public_id(f)


def _legacy_health_finding_id(f: Any, repository: str) -> str:
    """The pre-column id form, still accepted so a quoted one keeps resolving."""
    try:
        details = json.loads(f.details_json) if f.details_json else {}
    except (TypeError, json.JSONDecodeError):
        details = str(f.details_json or "")
    return stable_entity_id(
        "finding",
        repository,
        {
            "family": "health",
            "path": path_identity(f.file_path),
            "kind": f.biomarker_type,
            "symbol": f.function_name or "",
            "line_start": f.line_start,
            "line_end": f.line_end,
            "reason": f.reason or "",
            "details": details,
        },
    )


def _serialize_finding(f: HealthFinding, repository: str = "default") -> dict[str, Any]:
    try:
        details = json.loads(f.details_json) if f.details_json else {}
    except Exception:
        details = {}
    dimension = getattr(f, "dimension", None) or "defect"
    rank = (
        {"perf_rank": _perf_rank(f.biomarker_type, details)} if dimension == "performance" else {}
    )
    return {
        "id": _health_finding_id(f, repository),
        "repository": repository,
        "biomarker_type": f.biomarker_type,
        "severity": f.severity,
        "file_path": f.file_path,
        # A file-level finding has no symbol or span: absent rather than null.
        **({"function_name": f.function_name} if f.function_name else {}),
        **({"line_start": f.line_start} if f.line_start is not None else {}),
        **({"line_end": f.line_end} if f.line_end is not None else {}),
        "health_impact": round(f.health_impact, 3),
        "reason": f.reason,
        "details": details,
        "status": f.status,
        "dimension": dimension,
        # Performance rows only: a zero elsewhere would read as measured.
        **rank,
    }


def _refactoring_plan_id(r: Any, repository: str) -> str:
    """Unwrap a hydrated recommendation, then defer to the identity owner."""
    return refactoring_plan_id(r.suggestion if isinstance(r, Recommendation) else r, repository)


def _serialize_refactoring(
    r: Any, repository: str | None = None
) -> dict[str, Any]:
    """Compatibility adapter; request paths hydrate through the async service."""
    if isinstance(r, Recommendation):
        payload = r.as_dict()
    else:
        payload = build_recommendations([r])[0].as_dict()
    if repository is not None:
        payload["id"] = _refactoring_plan_id(r, repository)
        payload["repository"] = repository
    return payload


def _round_opt(v: Any) -> float | None:
    """Round a nullable per-dimension score, preserving ``None`` (not measured)."""
    return round(v, 2) if v is not None else None


def _serialize_metric(
    m: HealthFileMetric,
    lead: dict[str, Any] | None = None,
    *,
    is_test: bool = False,
) -> dict[str, Any]:
    return {
        "file_path": m.file_path,
        "score": round(m.score, 2),
        "max_ccn": m.max_ccn,
        "max_nesting": m.max_nesting,
        "nloc": m.nloc,
        # ``is_test``: this file is test material. ``has_test_file``: something
        # tests this file. Different questions, both kept.
        "is_test": is_test,
        "has_test_file": m.has_test_file,
        "line_coverage_pct": m.line_coverage_pct,
        "branch_coverage_pct": m.branch_coverage_pct,
        "module": m.module,
        # Leverage: ``(8.0 - score) * nloc``, what the headline recovers if this
        # file reaches target. See ``_gap_analysis`` for its denominator.
        "weighted_deficit": round(max(TARGET_SCORE - m.score, 0.0) * max(m.nloc, 1)),
        # ``defect_score`` is deliberately absent: it always equals ``score``.
        "maintainability_score": _round_opt(getattr(m, "maintainability_score", None)),
        "performance_score": _round_opt(getattr(m, "performance_score", None)),
        # Dominant cause and pre-clamp magnitude; null when the file has no findings.
        "primary_biomarker": lead.get("primary_biomarker") if lead else None,
        "primary_reason": lead.get("primary_reason") if lead else None,
        "total_deduction": lead.get("total_deduction") if lead else None,
    }
