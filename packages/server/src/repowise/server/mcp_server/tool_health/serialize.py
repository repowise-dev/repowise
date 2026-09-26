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

    Every performance finding carries ``health_impact: 0`` by construction, so
    without a key the list came back in file order and "which of these matters"
    was unanswerable from the payload.

    The weights live with the opportunity ranking rather than here. Two tables
    used to answer "which marker costs more" and they had already drifted apart
    on markers both named, so a finding and the opportunity built from it could
    disagree about the same evidence. Nothing here is blended into ``score`` or
    ``performance_score``; a caller who disagrees can re-rank from
    ``biomarker_type`` and ``details`` on the same row.
    """
    if not isinstance(details, dict):
        details = {}
    return observation_rank(
        biomarker_type, details.get("boundary_kind"), bool(details.get("cross_function"))
    )


def _rank_emitted(rows: list[Any]) -> list[Any]:
    """Break the ``health_impact`` ties that the performance dimension is made of.

    Rows arrive impact-ordered from SQL, which decides nothing among the
    performance findings: they all carry ``0``, so the head was whatever the tie
    broke to — file order. This re-sorts **within** each impact tier, so the
    defect ordering every other block is built on is untouched (identical
    impacts were already interchangeable) and the perf tier stops being
    alphabetical.

    ``file_path`` is the final key so the order is total and reproducible; two
    findings that rank the same used to swap places between calls on nothing.
    Rows with no ``details_json`` attribute — the narrow dashboard read, unless
    the caller filtered to ``performance`` — rank on the marker alone, which is
    exactly the tier where the rank cannot move a row anyway.
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

    Storage row ids are republished on every analysis, so they cannot be quoted
    back. This is the id evidence carries and the ``finding_id`` selector
    resolves, and it is a column, so resolving it is a seek.
    """
    stored = getattr(f, "public_id", None)
    return stored if isinstance(stored, str) and stored else finding_public_id(f)


def _legacy_health_finding_id(f: Any, repository: str) -> str:
    """The pre-column id form, still accepted so a quoted one keeps resolving.

    Its kernel held generated prose and derived detail keys, so it moved
    whenever a detector reworded itself or a later model changed its mind.
    """
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
        # A file-level finding has no symbol and no line span. Absent rather
        # than null, on the same rule as ``rank`` below: three null keys on
        # every such row is a bill, not a disclosure.
        **({"function_name": f.function_name} if f.function_name else {}),
        **({"line_start": f.line_start} if f.line_start is not None else {}),
        **({"line_end": f.line_end} if f.line_end is not None else {}),
        "health_impact": round(f.health_impact, 3),
        "reason": f.reason,
        "details": details,
        "status": f.status,
        # Health pillar this finding homes under (defect / maintainability /
        # performance) for per-dimension filtering.
        "dimension": dimension,
        # Performance rows only — see ``_perf_rank``. Absent everywhere else
        # rather than zero: a defect finding ranks on ``weighted_deficit`` and a
        # 0 here would read as "measured, and it is nothing".
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
        # Two different questions, deliberately both present: ``has_test_file``
        # is "does something test this file", ``is_test`` is "is this file
        # itself test material". Defect risk in a test reads differently from
        # defect risk in the code it covers, and nothing in the payload used to
        # say which one you were looking at.
        "is_test": is_test,
        "has_test_file": m.has_test_file,
        "line_coverage_pct": m.line_coverage_pct,
        "branch_coverage_pct": m.branch_coverage_pct,
        "module": m.module,
        # Leverage: NLOC-weighted points this file drags below the target score
        # (``(8.0 - score) * nloc``, 0 once the file is at target). This is
        # how much the repo headline recovers if the file reaches 8.0, so
        # ranking by it — not by raw score — points at the files that actually
        # move the average. A tiny 1.0 file and a 1200-line 1.0 file score the
        # same but differ 40x here.
        # The unit is score-points x NLOC, which is meaningless on its own — the
        # docstring and ``gap_analysis.weighted_gap_points`` give it a
        # denominator, and every ``high_leverage_files`` row carries the same
        # quantity as ``share_of_repo_gap_pct``.
        "weighted_deficit": round(max(TARGET_SCORE - m.score, 0.0) * max(m.nloc, 1)),
        # Per-dimension scores from the three-signal split. ``defect_score`` is
        # deliberately absent: ``engine.py`` sets it and ``score`` from the same
        # ``scores["defect"]`` value, so it was pure duplication on every row of
        # every response — measured on this repo, 3,314 of 3,314 rows had
        # ``score == defect_score`` and none was NULL. Two names for one number
        # cost an agent a source read to decide which to rank on, and the one to
        # rank on is neither (it is ``weighted_deficit``). ``score`` survives
        # because every doc, skill and UI already names it.
        # ``performance_score`` is computed but not yet surfaced as its own pillar.
        "maintainability_score": _round_opt(getattr(m, "maintainability_score", None)),
        "performance_score": _round_opt(getattr(m, "performance_score", None)),
        # Dominant-cause lead + pre-clamp magnitude (null when no findings for
        # this row). Lets a caller lead with the one reason and rank two floored
        # files by depth without re-reading every finding.
        "primary_biomarker": lead.get("primary_biomarker") if lead else None,
        "primary_reason": lead.get("primary_reason") if lead else None,
        "total_deduction": lead.get("total_deduction") if lead else None,
    }
