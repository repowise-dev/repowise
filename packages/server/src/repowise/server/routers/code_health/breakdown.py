"""Reconstruct per-category score breakdowns from stored findings."""

from __future__ import annotations

import json
from typing import Any

from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import (
    CATEGORY_CAPS,
    biomarker_category,
    biomarker_weight,
    severity_deduction,
)


def _finding_details(f: Any) -> dict:
    """Return a finding's details as a dict, from either a live ``details``
    attr (tests) or the stored ``details_json`` column (the ORM row)."""
    d = getattr(f, "details", None)
    if isinstance(d, dict):
        return d
    raw = getattr(f, "details_json", None)
    if raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _finding_base_deduction(f: Any) -> float:
    """The pre-cap, pre-weight base deduction for one stored finding.

    Mirrors ``scoring.score_file``: a continuous ``deduction`` override (e.g.
    coverage scaled by the uncovered fraction, recorded in the finding's
    ``details``) takes the place of the discrete severity table. Reading the
    override here — instead of always recomputing from the severity band — is
    what lets the breakdown show the continuous coverage gradient rather than a
    band proxy.
    """
    override = _finding_details(f).get("deduction")
    if isinstance(override, (int, float)):
        return float(override)
    sev = Severity(f.severity) if not isinstance(f.severity, Severity) else f.severity
    return severity_deduction(sev)


def _score_breakdown_from_findings(findings: list[Any]) -> dict:
    """Reconstruct per-category deductions from open findings of one file.

    The applied per-finding impact is read from the **stored**
    ``health_impact`` (the exact, already-weighted-and-capped value computed by
    ``scoring.score_file`` at index time), so the breakdown reproduces the
    file's score and surfaces continuous signals (the coverage gradient) instead
    of a severity-band proxy. The raw (pre-cap) figure is reconstructed with the
    same ``base x weight`` formula scoring uses, so a capped category is honest
    about how much it shed.
    """
    per_cat: dict[str, list[Any]] = {}
    for f in findings:
        per_cat.setdefault(biomarker_category(f.biomarker_type), []).append(f)

    categories: list[dict] = []
    total_deduction = 0.0
    for cat, cap in CATEGORY_CAPS.items():
        entries = per_cat.get(cat, [])
        if not entries:
            continue
        raw_per_finding = [
            _finding_base_deduction(f) * biomarker_weight(f.biomarker_type) for f in entries
        ]
        applied_per_finding = [float(f.health_impact or 0.0) for f in entries]
        raw_sum = sum(raw_per_finding)
        applied_sum = sum(applied_per_finding)
        categories.append(
            {
                "category": cat,
                "cap": round(cap, 2),
                "raw_deduction": round(raw_sum, 3),
                "applied_deduction": round(applied_sum, 3),
                # Category shed weight iff its applied total is held at the cap.
                "capped": applied_sum < raw_sum - 1e-6,
                "finding_count": len(entries),
                "findings": [
                    {
                        "id": f.id,
                        "biomarker_type": f.biomarker_type,
                        "severity": f.severity,
                        "raw_impact": round(raw, 3),
                        "applied_impact": round(applied, 3),
                        "function_name": f.function_name,
                        "reason": f.reason,
                    }
                    for f, raw, applied in zip(
                        entries, raw_per_finding, applied_per_finding, strict=True
                    )
                ],
            }
        )
        total_deduction += applied_sum
    score = max(1.0, min(10.0, 10.0 - total_deduction))
    return {
        "score": round(score, 2),
        "total_deduction": round(total_deduction, 3),
        "categories": categories,
    }
