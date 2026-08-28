"""Module rollups + severity / biomarker breakdowns over finding lists."""

from __future__ import annotations

from typing import Any


def _module_rollups(metrics: list[Any]) -> list[dict]:
    """NLOC-weighted module rollups derived from ``HealthFileMetric.module``."""
    buckets: dict[str, list[Any]] = {}
    for m in metrics:
        if m.module:
            buckets.setdefault(m.module, []).append(m)
    rows: list[dict] = []
    for name, group in buckets.items():
        total_nloc = sum(max(r.nloc, 1) for r in group)
        avg = sum(r.score * max(r.nloc, 1) for r in group) / total_nloc if total_nloc else 10.0
        worst = min(group, key=lambda r: r.score)
        rows.append(
            {
                "module": name,
                "file_count": len(group),
                "nloc": sum(r.nloc for r in group),
                "average_health": round(avg, 2),
                "worst_performer_path": worst.file_path,
                "worst_performer_score": round(worst.score, 2),
            }
        )
    rows.sort(key=lambda r: r["average_health"])
    return rows


def _severity_breakdown(findings: list[Any]) -> dict[str, int]:
    out = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        s = (f.severity or "").lower()
        if s in out:
            out[s] += 1
    return out


def _biomarker_breakdown(findings: list[Any]) -> list[dict]:
    """Per-biomarker counts split by severity, sorted by total."""
    by_type: dict[str, dict[str, int]] = {}
    for f in findings:
        b = f.biomarker_type
        sev = (f.severity or "").lower()
        bucket = by_type.setdefault(
            b, {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        )
        if sev in bucket:
            bucket[sev] += 1
        bucket["total"] += 1
    rows = [{"biomarker_type": b, **counts} for b, counts in by_type.items()]
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows
