"""/api/repos/{repo_id}/health/* — code-health endpoints.

Distinct from ``routers/health.py`` (liveness / Prometheus). All routes
here require API-key auth and operate on the ``health_findings`` /
``health_file_metrics`` tables.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.churn_complexity import churn_complexity_points
from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
from repowise.core.analysis.health.grading import band_for
from repowise.core.analysis.health.grading import distribution as health_distribution
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.scoring import (
    CATEGORY_CAPS,
    biomarker_category,
    biomarker_weight,
    severity_deduction,
)
from repowise.core.analysis.health.signals import FileSignals, file_signals
from repowise.core.analysis.health.suggestions import suggestion_for as _suggestion_for
from repowise.core.analysis.health.trends import (
    FileTrend,
    diff_snapshots,
    file_trend,
    recent_kpis,
)
from repowise.core.persistence import crud
from repowise.core.persistence.models import WikiSymbol
from repowise.server.deps import get_db_session, verify_api_key

router = APIRouter(
    tags=["code-health"],
    dependencies=[Depends(verify_api_key)],
)


_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _finding_to_dict(f: Any) -> dict:
    try:
        details = json.loads(f.details_json) if f.details_json else {}
    except Exception:
        details = {}
    return {
        "id": f.id,
        "file_path": f.file_path,
        "biomarker_type": f.biomarker_type,
        "severity": f.severity,
        "function_name": f.function_name,
        "line_start": f.line_start,
        "line_end": f.line_end,
        "health_impact": round(f.health_impact, 3),
        "reason": f.reason,
        "details": details,
        "status": f.status,
    }


def _metric_to_dict(m: Any) -> dict:
    return {
        "file_path": m.file_path,
        "score": round(m.score, 2),
        "max_ccn": m.max_ccn,
        "max_nesting": m.max_nesting,
        "nloc": m.nloc,
        "has_test_file": m.has_test_file,
        "line_coverage_pct": m.line_coverage_pct,
        "module": m.module,
        "duplication_pct": getattr(m, "duplication_pct", None),
    }


def _file_trend_to_dict(t: FileTrend) -> dict:
    """Wire shape for ``FileHealthTrend`` (types/health.ts). ``points`` is
    empty on thin history so the UI shows a "no history yet" state."""
    return {
        "file_path": t.file_path,
        "points": [
            {
                "taken_at": p.taken_at.isoformat() if p.taken_at else None,
                "score": round(p.score, 2),
            }
            for p in t.points
        ],
        "current": t.current,
        "previous": t.previous,
        "delta": t.delta,
        "declining": t.declining,
        "snapshot_count": t.snapshot_count,
    }


def _file_signals_to_dict(s: FileSignals) -> dict:
    """Wire shape for ``FileSignals`` (types/health.ts). Each value is null
    when its source row is absent so the UI shows "no signal", never a
    misleading zero. ``change_entropy_pct`` is 0-100 (the column is 0-1)."""
    return {
        "prior_defect_count": s.prior_defect_count,
        "change_entropy_pct": s.change_entropy_pct,
        "lines_added_90d": s.lines_added_90d,
        "lines_deleted_90d": s.lines_deleted_90d,
        "commit_count_90d": s.commit_count_90d,
        "age_days": s.age_days,
        "primary_owner_name": s.primary_owner_name,
        "primary_owner_commit_pct": s.primary_owner_commit_pct,
        "recent_owner_name": s.recent_owner_name,
        "recent_owner_commit_pct": s.recent_owner_commit_pct,
        "in_degree": s.in_degree,
        "out_degree": s.out_degree,
    }


async def _load_file_signals(session: AsyncSession, repo_id: str, file_path: str) -> FileSignals:
    """Join git metadata + graph degree for one file (read-only, no recompute).

    Degree is read only when the file is a graph node so topology stays "no
    signal" for files absent from the graph, rather than reporting a spurious
    zero. Shared by the drawer breakdown and the file-detail aggregate.
    """
    git_meta = await crud.get_git_metadata(session, repo_id, file_path)
    node = await crud.get_graph_node(session, repo_id, file_path)
    degrees = (
        await crud.get_node_degree_counts(session, repo_id, file_path)
        if node is not None
        else None
    )
    return file_signals(git_meta, degrees)


async def _attach_symbol_ids(
    session: AsyncSession, repo_id: str, finding_dicts: list[dict]
) -> list[dict]:
    """Attach the matching ``WikiSymbol.symbol_id`` to function-level findings.

    Matched by exact (file_path, function_name), falling back to the symbol
    whose line span contains the finding's ``line_start`` (covers methods
    recorded as ``Class.method`` vs the bare symbol name). One query for the
    whole batch; findings with no match keep ``symbol_id = None`` so the UI
    can degrade to the file page.
    """
    paths = {d["file_path"] for d in finding_dicts if d.get("function_name")}
    if not paths:
        return finding_dicts
    rows = (
        await session.execute(
            select(
                WikiSymbol.symbol_id,
                WikiSymbol.file_path,
                WikiSymbol.name,
                WikiSymbol.start_line,
                WikiSymbol.end_line,
            ).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path.in_(paths),
            )
        )
    ).all()
    by_name: dict[tuple[str, str], str] = {}
    by_file: dict[str, list[tuple[int, int, str]]] = {}
    for symbol_id, file_path, name, start_line, end_line in rows:
        by_name.setdefault((file_path, name), symbol_id)
        if name and "." in name:
            by_name.setdefault((file_path, name.rsplit(".", 1)[-1]), symbol_id)
        if start_line is not None and end_line is not None:
            by_file.setdefault(file_path, []).append((start_line, end_line, symbol_id))
    for d in finding_dicts:
        fn = d.get("function_name")
        if not fn:
            d["symbol_id"] = None
            continue
        sid = by_name.get((d["file_path"], fn))
        if sid is None and d.get("line_start") is not None:
            line = d["line_start"]
            spans = by_file.get(d["file_path"], [])
            # Narrowest enclosing span wins (a method, not its class).
            best: tuple[int, str] | None = None
            for start, end, symbol_id in spans:
                if start <= line <= end and (best is None or end - start < best[0]):
                    best = (end - start, symbol_id)
            sid = best[1] if best else None
        d["symbol_id"] = sid
    return finding_dicts


# Strip the trailing " (N)" suffix that community detection appends to
# disambiguate same-named modules. The leak is harmless in the DB but
# noisy in the dashboard.
_MODULE_SUFFIX = re.compile(r"\s*\(\d+\)\s*$")


def _clean_module(name: str) -> str:
    return _MODULE_SUFFIX.sub("", name).strip()


def _module_rollups(metrics: list[Any]) -> list[dict]:
    """NLOC-weighted module rollups derived from ``HealthFileMetric.module``."""
    buckets: dict[str, list[Any]] = {}
    for m in metrics:
        if m.module:
            buckets.setdefault(_clean_module(m.module), []).append(m)
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


@router.get("/api/repos/{repo_id}/health/overview")
async def health_overview(
    repo_id: str,
    limit: int = Query(20, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """KPIs + lowest-scoring files + per-module rollup + meta."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    summary = await crud.get_health_summary(session, repo_id)
    metrics = await crud.get_health_metrics(session, repo_id)
    findings = await crud.get_health_findings(session, repo_id)
    snapshots = await crud.list_health_snapshots(session, repo_id)

    # Pull hotspot_health from the latest snapshot (KPIs aren't recomputed
    # on every overview hit — the snapshot is authoritative).
    hotspot_health: float | None = None
    last_indexed_at: str | None = None
    if snapshots:
        latest = snapshots[-1]
        hotspot_health = round(float(latest.hotspot_health), 2)
        last_indexed_at = latest.taken_at.isoformat() if latest.taken_at else None

    metric_dicts = [_metric_to_dict(m) for m in metrics]

    # Repo-level band (from the NLOC-weighted average) + the per-band file
    # distribution. Both derive purely from the existing score — no new data.
    avg = summary.get("average_health")
    summary = {
        **summary,
        "hotspot_health": hotspot_health,
        "severity_breakdown": _severity_breakdown(findings),
        "band": band_for(float(avg)) if avg is not None else None,
    }
    distribution = health_distribution(metric_dicts)

    # "Does the score find the bugs?" self-validation, derived from the same
    # metrics + findings (prior_defect biomarker) already loaded above. ``None``
    # when the repo lacks enough files / defect history to be honest.
    defect_accuracy = compute_defect_accuracy(
        [_metric_to_dict(m) for m in metrics],
        [_finding_to_dict(f) for f in findings],
    )

    top_findings = await _attach_symbol_ids(
        session, repo_id, [_finding_to_dict(f) for f in findings[:limit]]
    )

    return {
        "summary": summary,
        "distribution": distribution,
        "defect_accuracy": defect_accuracy,
        "files": metric_dicts[:limit],
        "top_findings": top_findings,
        "modules": _module_rollups(metrics),
        "biomarkers": _biomarker_breakdown(findings),
        "meta": {
            "last_indexed_at": last_indexed_at,
            "head_commit": repo.head_commit,
            "snapshot_count": len(snapshots),
        },
    }


# Shields-compatible band colors. Named colors for the JSON endpoint (shields
# resolves them) + hexes for the self-rendered SVG so it matches without a
# round-trip to img.shields.io.
_BADGE_COLOR_NAME: dict[str, str] = {
    "healthy": "brightgreen",
    "warning": "yellow",
    "alert": "red",
    "unknown": "lightgrey",
}
_BADGE_COLOR_HEX: dict[str, str] = {
    "brightgreen": "#4c1",
    "yellow": "#dfb317",
    "red": "#e05d44",
    "lightgrey": "#9f9f9f",
}


def _badge_fields(average_health: float | None) -> tuple[str, str, str, str]:
    """Return ``(label, message, color_name, band)`` for the health badge."""
    if average_health is None:
        return "health", "no data", _BADGE_COLOR_NAME["unknown"], "unknown"
    band = band_for(float(average_health))
    return "health", f"{average_health:.1f}/10", _BADGE_COLOR_NAME[band], band


def _render_badge_svg(label: str, message: str, color_name: str) -> str:
    """Render a flat shields-style SVG so the badge needs no external service.

    Char-width estimate matches shields' Verdana ~7px/char heuristic; exact
    pixel fidelity isn't needed for a README badge.
    """
    hex_color = _BADGE_COLOR_HEX.get(color_name, "#9f9f9f")
    lw = len(label) * 7 + 10
    mw = len(message) * 7 + 10
    total = lw + mw
    lx = lw * 10 // 2
    mx = (lw + mw // 2) * 10
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" '
        f'role="img" aria-label="{label}: {message}">'
        f"<title>{label}: {message}</title>"
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{lw}" height="20" fill="#555"/>'
        f'<rect x="{lw}" width="{mw}" height="20" fill="{hex_color}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/></g>'
        f'<g fill="#fff" text-anchor="middle" '
        f'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="110">'
        f'<text x="{lx}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" '
        f'textLength="{(lw - 10) * 10}">{label}</text>'
        f'<text x="{lx}" y="140" transform="scale(.1)" textLength="{(lw - 10) * 10}">{label}</text>'
        f'<text x="{mx}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" '
        f'textLength="{(mw - 10) * 10}">{message}</text>'
        f'<text x="{mx}" y="140" transform="scale(.1)" textLength="{(mw - 10) * 10}">{message}</text>'
        f"</g></svg>"
    )


async def _badge_average_health(session: AsyncSession, repo_id: str) -> float | None:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    summary = await crud.get_health_summary(session, repo_id)
    avg = summary.get("average_health")
    return float(avg) if avg is not None else None


@router.get("/api/repos/{repo_id}/health/badge.json")
async def health_badge_json(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Shields.io endpoint-badge payload (color + ``N.N/10`` score, no letter).

    Embed via ``https://img.shields.io/endpoint?url=<this-url>``.
    """
    avg = await _badge_average_health(session, repo_id)
    label, message, color, band = _badge_fields(avg)
    return {
        "schemaVersion": 1,
        "label": label,
        "message": message,
        "color": color,
        "band": band,
    }


@router.get("/api/repos/{repo_id}/health/badge.svg")
async def health_badge_svg(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Self-rendered flat SVG health badge (no external service round-trip)."""
    avg = await _badge_average_health(session, repo_id)
    label, message, color, _band = _badge_fields(avg)
    svg = _render_badge_svg(label, message, color)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "max-age=300, public"},
    )


@router.get("/api/repos/{repo_id}/health/modules")
async def health_modules(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """NLOC-weighted module rollups for the dashboard module section."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    metrics = await crud.get_health_metrics(session, repo_id)
    return {"modules": _module_rollups(metrics)}


@router.get("/api/repos/{repo_id}/health/findings")
async def list_health_findings(
    repo_id: str,
    biomarker_type: str | None = Query(None),
    file_path: str | None = Query(None),
    min_severity: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[dict]:
    findings = await crud.get_health_findings(
        session,
        repo_id,
        biomarker_type=biomarker_type,
        file_path=file_path,
        min_severity=min_severity,
    )
    return await _attach_symbol_ids(
        session, repo_id, [_finding_to_dict(f) for f in findings[:limit]]
    )


_SORT_FIELDS = {
    "score",
    "max_ccn",
    "max_nesting",
    "nloc",
    "duplication_pct",
    "line_coverage_pct",
    "file_path",
}


@router.get("/api/repos/{repo_id}/health/files")
async def list_health_files(
    repo_id: str,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    sort: str = Query("score", description="Sort field"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    search: str | None = Query(None, description="Substring filter on file_path"),
    module: str | None = Query(None, description="Filter to a module prefix"),
    only_hotspots: bool = Query(False),
    only_untested: bool = Query(False),
    only_failing: bool = Query(False, description="score < 7"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    if sort not in _SORT_FIELDS:
        sort = "score"
    metrics = await crud.get_health_metrics(session, repo_id)

    hotspot_paths: set[str] = set()
    if only_hotspots:
        git_meta = await crud.get_all_git_metadata(session, repo_id)
        hotspot_paths = {p for p, gm in git_meta.items() if getattr(gm, "is_hotspot", False)}

    def _keep(m: Any) -> bool:
        if search and search.lower() not in m.file_path.lower():
            return False
        if module and not m.file_path.startswith(module):
            return False
        if only_hotspots and m.file_path not in hotspot_paths:
            return False
        if only_untested and m.has_test_file:
            return False
        return not (only_failing and m.score >= 7)

    filtered = [m for m in metrics if _keep(m)]

    def _key(m: Any):
        v = getattr(m, sort, None)
        if v is None:
            return (1, 0) if order == "asc" else (0, 0)
        return (0, v) if order == "asc" else (0, -v if isinstance(v, (int, float)) else v)

    reverse = order == "desc" and sort == "file_path"
    if sort == "file_path":
        filtered.sort(key=lambda m: m.file_path, reverse=reverse)
    else:
        filtered.sort(key=_key, reverse=False)

    total = len(filtered)
    page = filtered[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "files": [_metric_to_dict(m) for m in page],
    }


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


@router.get("/api/repos/{repo_id}/health/files/breakdown")
async def file_score_breakdown(
    repo_id: str,
    file_path: str = Query(..., description="File path to break down"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    metrics = await crud.get_health_metrics(session, repo_id, file_paths=[file_path])
    metric = metrics[0] if metrics else None
    findings = await crud.get_health_findings(session, repo_id, file_path=file_path)
    breakdown = _score_breakdown_from_findings(findings)
    finding_dicts = await _attach_symbol_ids(
        session, repo_id, [_finding_to_dict(f) for f in findings]
    )
    snapshots = await crud.list_health_snapshots(session, repo_id)
    return {
        "file_path": file_path,
        "metric": _metric_to_dict(metric) if metric else None,
        "breakdown": breakdown,
        "findings": finding_dicts,
        "suggestions": {b: _suggestion_for(b) for b in {f.biomarker_type for f in findings}},
        "trend": _file_trend_to_dict(file_trend(snapshots, file_path)),
        "signals": _file_signals_to_dict(await _load_file_signals(session, repo_id, file_path)),
    }


@router.get("/api/repos/{repo_id}/health/files/trend")
async def file_health_trend(
    repo_id: str,
    file_path: str = Query(..., description="File path to chart over time"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """A single file's score-over-time series from the snapshot history.

    Silent (empty ``points``) when fewer than two snapshots carry the file.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    snapshots = await crud.list_health_snapshots(session, repo_id)
    return _file_trend_to_dict(file_trend(snapshots, file_path))


@router.get("/api/repos/{repo_id}/health/trend")
async def health_trend(
    repo_id: str,
    limit: int = Query(20, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    snapshots = await crud.list_health_snapshots(session, repo_id)
    summary = diff_snapshots(snapshots)

    # Per-file delta from the last two snapshots.
    file_deltas: list[dict] = []
    if len(snapshots) >= 2:
        try:
            prev = json.loads(snapshots[-2].per_file_scores_json or "{}")
            cur = json.loads(snapshots[-1].per_file_scores_json or "{}")
        except Exception:
            prev, cur = {}, {}
        all_paths = set(prev) | set(cur)
        for p in all_paths:
            before = prev.get(p)
            after = cur.get(p)
            if before is None or after is None:
                continue
            d = round(float(after) - float(before), 2)
            if d == 0:
                continue
            file_deltas.append({"file_path": p, "before": before, "after": after, "delta": d})
        file_deltas.sort(key=lambda r: r["delta"])

    return {
        "history": recent_kpis(snapshots, limit=limit),
        "summary": {
            "current_hotspot_health": summary.current_hotspot_health,
            "current_average_health": summary.current_average_health,
            "previous_hotspot_health": summary.previous_hotspot_health,
            "previous_average_health": summary.previous_average_health,
            "hotspot_delta": summary.hotspot_delta,
            "average_delta": summary.average_delta,
        },
        "alerts": [
            {
                "kind": a.kind,
                "metric": a.metric,
                "current": a.current,
                "baseline": a.baseline,
                "delta": a.delta,
                "message": a.message,
            }
            for a in summary.alerts
        ],
        "file_deltas": file_deltas[:50],
        "snapshot_count": len(snapshots),
    }


class FindingStatusUpdate(BaseModel):
    status: str = Field(..., description="open | acknowledged | resolved | false_positive")


_ALLOWED_STATUSES = {"open", "acknowledged", "resolved", "false_positive"}


@router.patch("/api/repos/{repo_id}/health/findings/{finding_id}")
async def update_finding_status(
    repo_id: str,
    finding_id: str,
    payload: FindingStatusUpdate,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    if payload.status not in _ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400, detail=f"status must be one of {sorted(_ALLOWED_STATUSES)}"
        )
    f = await crud.update_health_finding_status(session, finding_id, payload.status)
    if f is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    await session.commit()
    return _finding_to_dict(f)


def _coverage_row_to_dict(row: Any, *, include_covered_lines: bool = False) -> dict:
    out: dict[str, Any] = {
        "file_path": row.file_path,
        "source_format": row.source_format,
        "line_coverage_pct": row.line_coverage_pct,
        "branch_coverage_pct": row.branch_coverage_pct,
        "total_coverable_lines": row.total_coverable_lines,
        "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
        "ingested_commit_sha": row.ingested_commit_sha,
    }
    if include_covered_lines:
        try:
            out["covered_lines"] = json.loads(row.covered_lines_json or "[]")
        except Exception:
            out["covered_lines"] = []
    return out


@router.get("/api/repos/{repo_id}/health/coverage")
async def health_coverage(
    repo_id: str,
    file_path: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Coverage summary + per-file rows.

    Pass ``file_path`` to fetch a single file's full covered-line set.
    Without ``file_path`` we return the summary + a list of per-file
    rows trimmed by ``limit`` (covered_lines arrays stripped).
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    summary = await crud.get_coverage_summary(session, repo_id)
    if summary.get("ingested_at") is not None:
        summary = {**summary, "ingested_at": summary["ingested_at"].isoformat()}
    rows = await crud.load_coverage_for_repo(
        session, repo_id, file_paths=[file_path] if file_path else None
    )
    metrics = await crud.get_health_metrics(session, repo_id)
    metric_by_path = {m.file_path: m for m in metrics}

    if file_path:
        files = [_coverage_row_to_dict(r, include_covered_lines=True) for r in rows]
    else:
        rows_sorted = sorted(rows, key=lambda r: r.line_coverage_pct)
        files = [_coverage_row_to_dict(r) for r in rows_sorted[:limit]]
        # Attach per-file health score so the UI can render a coverage
        # x score matrix without a second request.
        for f in files:
            m = metric_by_path.get(f["file_path"])
            if m is not None:
                f["health_score"] = round(m.score, 2)
                f["nloc"] = m.nloc

    # Aggregate by directory for module-level bars (cheap; one pass).
    modules: dict[str, dict[str, Any]] = {}
    for r in rows:
        mod = r.file_path.rsplit("/", 1)[0] if "/" in r.file_path else "(root)"
        bucket = modules.setdefault(mod, {"covered": 0, "total": 0, "files": 0})
        bucket["files"] += 1
        bucket["total"] += r.total_coverable_lines
        bucket["covered"] += round(r.line_coverage_pct / 100.0 * r.total_coverable_lines)
    module_rows = [
        {
            "module": name,
            "files": v["files"],
            "covered_lines": v["covered"],
            "total_lines": v["total"],
            "line_coverage_pct": (
                round(v["covered"] / v["total"] * 100.0, 2) if v["total"] else 0.0
            ),
        }
        for name, v in modules.items()
    ]
    module_rows.sort(key=lambda x: x["line_coverage_pct"])

    return {
        "summary": summary,
        "files": files,
        "modules": module_rows[:limit],
    }


_EFFORT_BUCKETS: tuple[tuple[int, str], ...] = (
    (40, "S"),
    (150, "M"),
    (400, "L"),
)


def _effort_for_nloc(nloc: int) -> str:
    for ceiling, label in _EFFORT_BUCKETS:
        if nloc <= ceiling:
            return label
    return "XL"


@router.get("/api/repos/{repo_id}/health/refactoring-targets")
async def refactoring_targets(
    repo_id: str,
    limit: int = Query(200, ge=1, le=500),
    module: str | None = Query(None, description="Filter to files in this module path"),
    biomarker: str | None = Query(None, description="Filter to one biomarker type"),
    min_severity: str | None = Query(None),
    max_effort: str | None = Query(None, description="S | M | L | XL"),
    sort: str = Query(
        "impact_per_effort", pattern="^(impact_per_effort|total_impact|score|finding_count)$"
    ),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Refactoring candidates ranked by impact / effort."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    metrics = await crud.get_health_metrics(session, repo_id)
    metric_by_path = {m.file_path: m for m in metrics}
    findings = await crud.get_health_findings(session, repo_id)

    by_file: dict[str, list[Any]] = {}
    for f in findings:
        if biomarker and f.biomarker_type != biomarker:
            continue
        if min_severity:
            order = _SEVERITY_ORDER
            if order.get(f.severity, 0) < order.get(min_severity, 0):
                continue
        by_file.setdefault(f.file_path, []).append(f)

    effort_rank = {"S": 1, "M": 2, "L": 3, "XL": 5}
    max_effort_rank = effort_rank.get(max_effort or "", 99)

    targets: list[dict] = []
    for file_path, fs in by_file.items():
        if module and not file_path.startswith(module):
            continue
        m = metric_by_path.get(file_path)
        nloc = m.nloc if m is not None else 0
        score = m.score if m is not None else 10.0
        primary = max(fs, key=lambda x: x.health_impact)
        total_impact = round(sum(x.health_impact for x in fs), 3)
        effort_bucket = _effort_for_nloc(nloc)
        if effort_rank[effort_bucket] > max_effort_rank:
            continue
        weight = effort_rank[effort_bucket]
        ratio = round(total_impact / weight, 3)
        targets.append(
            {
                "file_path": file_path,
                "score": round(score, 2),
                "nloc": nloc,
                "module": _clean_module(m.module) if (m and m.module) else None,
                "primary_biomarker": primary.biomarker_type,
                "primary_severity": primary.severity,
                "primary_reason": primary.reason,
                "primary_function": primary.function_name,
                "primary_line_start": primary.line_start,
                "primary_line_end": primary.line_end,
                "primary_suggestion": _suggestion_for(primary.biomarker_type),
                "primary_finding_id": primary.id,
                "total_impact": total_impact,
                "finding_count": len(fs),
                "biomarkers": sorted({x.biomarker_type for x in fs}),
                "effort_bucket": effort_bucket,
                "impact_per_effort": ratio,
                "all_findings": [_finding_to_dict(f) for f in fs],
            }
        )

    sort_key_map = {
        "impact_per_effort": lambda t: (-t["impact_per_effort"], -t["total_impact"]),
        "total_impact": lambda t: -t["total_impact"],
        "score": lambda t: t["score"],
        "finding_count": lambda t: -t["finding_count"],
    }
    targets.sort(key=sort_key_map[sort])
    return {"targets": targets[:limit], "total": len(targets)}


def _churn_complexity_to_dict(p: Any) -> dict:
    return {
        "file_path": p.file_path,
        "commit_count_90d": p.commit_count_90d,
        "max_ccn": p.max_ccn,
        "nloc": p.nloc,
        "score": p.score,
        "churn_percentile": p.churn_percentile,
    }


@router.get("/api/repos/{repo_id}/health/churn-complexity")
async def churn_complexity(
    repo_id: str,
    limit: int = Query(300, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    """Churn x complexity scatter points -- the "hotspot anatomy" danger-zone view.

    One point per recently-changed file: x = 90-day commit count (churn),
    y = max cyclomatic complexity, dot size = NLOC, color = health band. The
    top-right corner is where churn and complexity collide -- the highest-value
    refactoring targets, plotted instead of listed.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    metrics = await crud.get_health_metrics(session, repo_id)
    git_meta = await crud.get_all_git_metadata(session, repo_id)
    points = churn_complexity_points(metrics, git_meta)
    return {
        "points": [_churn_complexity_to_dict(p) for p in points[:limit]],
        "total": len(points),
    }
