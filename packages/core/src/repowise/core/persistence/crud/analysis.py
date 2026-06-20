"""CRUD operations for the analysis domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.dead_code.risk_factors import effective_safe_to_delete

from ..models import (
    CoverageFile,
    DeadCodeFinding,
    HealthFileMetric,
    HealthFinding,
    HealthSnapshot,
    _new_uuid,
    _now_utc,
)
from ._shared import _BATCH_SIZE

# ---------------------------------------------------------------------------
# DeadCodeFinding CRUD
# ---------------------------------------------------------------------------


def _finding_file_path(finding: Any) -> str | None:
    """Read ``file_path`` from a dataclass-like finding or a plain dict."""
    if isinstance(finding, dict):
        return finding.get("file_path")
    return getattr(finding, "file_path", None)


def _dead_code_row_kwargs(finding: Any, repository_id: str) -> dict:
    """Normalize a DeadCodeFindingData-like object or plain dict into kwargs
    for the ``DeadCodeFinding`` ORM row."""
    if hasattr(finding, "kind"):
        data = {
            "kind": str(finding.kind.value)
            if hasattr(finding.kind, "value")
            else str(finding.kind),
            "file_path": finding.file_path,
            "symbol_name": finding.symbol_name,
            "symbol_kind": finding.symbol_kind,
            "confidence": finding.confidence,
            "reason": finding.reason,
            "last_commit_at": finding.last_commit_at,
            "commit_count_90d": finding.commit_count_90d,
            "lines": finding.lines,
            "package": finding.package,
            "evidence_json": json.dumps(
                finding.evidence if hasattr(finding, "evidence") else []
            ),
            "safe_to_delete": finding.safe_to_delete,
            "primary_owner": finding.primary_owner,
            "age_days": finding.age_days,
        }
    else:
        data = dict(finding)
        if "evidence" in data:
            data["evidence_json"] = json.dumps(data.pop("evidence"))

    return {
        "id": _new_uuid(),
        "repository_id": repository_id,
        **{
            k: v
            for k, v in data.items()
            if k not in ("id", "repository_id") and hasattr(DeadCodeFinding, k)
        },
    }


async def save_dead_code_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[dict],
) -> None:
    """Persist dead code findings, replacing any existing open findings for the repo."""
    # Delete existing open findings for this repo before saving new ones
    existing = await session.execute(
        select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repository_id,
            DeadCodeFinding.status == "open",
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)

    for i in range(0, len(findings), _BATCH_SIZE):
        batch = findings[i : i + _BATCH_SIZE]
        for finding in batch:
            session.add(DeadCodeFinding(**_dead_code_row_kwargs(finding, repository_id)))
        await session.flush()


async def upsert_dead_code_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
    *,
    file_paths: list[str],
) -> None:
    """Replace open dead-code findings **only for the given file paths**.

    Used by the incremental ``repowise update`` path so unchanged files keep
    their findings instead of being wiped on every partial re-index. Callers
    must pass the full set of *changed* file paths (not just paths that
    produced findings) so a changed-but-now-clean file has its stale findings
    removed.
    """
    if not file_paths:
        return
    allowed = set(file_paths)
    existing = await session.execute(
        select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repository_id,
            DeadCodeFinding.status == "open",
            DeadCodeFinding.file_path.in_(file_paths),
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    # Insert only within the replaced scope (delete is scoped to file_paths).
    scoped = [f for f in findings if _finding_file_path(f) in allowed]
    for i in range(0, len(scoped), _BATCH_SIZE):
        batch = scoped[i : i + _BATCH_SIZE]
        for finding in batch:
            session.add(DeadCodeFinding(**_dead_code_row_kwargs(finding, repository_id)))
        await session.flush()


async def get_dead_code_findings(
    session: AsyncSession,
    repository_id: str,
    *,
    kind: str | None = None,
    min_confidence: float = 0.0,
    status: str = "open",
) -> list[DeadCodeFinding]:
    """Return dead code findings filtered by kind, confidence, and status."""
    q = select(DeadCodeFinding).where(
        DeadCodeFinding.repository_id == repository_id,
        DeadCodeFinding.status == status,
        DeadCodeFinding.confidence >= min_confidence,
    )
    if kind is not None:
        q = q.where(DeadCodeFinding.kind == kind)
    q = q.order_by(DeadCodeFinding.confidence.desc())
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_dead_code_status(
    session: AsyncSession,
    finding_id: str,
    status: str,
    note: str | None = None,
) -> DeadCodeFinding | None:
    """Update the status (and optional note) of a dead code finding."""
    finding = await session.get(DeadCodeFinding, finding_id)
    if finding is None:
        return None
    finding.status = status
    if note is not None:
        finding.note = note
    await session.flush()
    return finding


async def get_dead_code_summary(session: AsyncSession, repository_id: str) -> dict:
    """Return aggregate dead code statistics."""
    result = await session.execute(
        select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repository_id,
            DeadCodeFinding.status == "open",
        )
    )
    findings = list(result.scalars().all())

    summary: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    total_lines = 0
    by_kind: dict[str, int] = {}

    for f in findings:
        if f.confidence >= 0.7:
            summary["high"] += 1
        elif f.confidence >= 0.4:
            summary["medium"] += 1
        else:
            summary["low"] += 1
        total_lines += f.lines
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    # Re-derive effective safety from confidence + path risk factors rather
    # than trusting the persisted boolean alone, so findings written before the
    # risk-factor logic existed (or in a config/bootstrap/database/environment
    # file the allowlist missed) are not counted as deletion-ready.
    deletable_lines = sum(
        f.lines
        for f in findings
        if effective_safe_to_delete(f.confidence, f.file_path, f.safe_to_delete)
    )

    return {
        "total_findings": len(findings),
        "confidence_summary": summary,
        "deletable_lines": deletable_lines,
        "total_lines": total_lines,
        "by_kind": by_kind,
    }


# ---------------------------------------------------------------------------
# Code Health CRUD
# ---------------------------------------------------------------------------


async def save_health_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
) -> None:
    """Replace open health findings for *repository_id* with *findings*.

    Mirrors ``save_dead_code_findings`` — delete-then-insert. Accepts
    either ``HealthFindingData`` dataclasses or plain dicts.
    """
    existing = await session.execute(
        select(HealthFinding).where(
            HealthFinding.repository_id == repository_id,
            HealthFinding.status == "open",
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)

    for i in range(0, len(findings), _BATCH_SIZE):
        batch = findings[i : i + _BATCH_SIZE]
        for f in batch:
            if hasattr(f, "biomarker_type"):
                severity = f.severity
                severity_str = str(severity.value) if hasattr(severity, "value") else str(severity)
                data = {
                    "file_path": f.file_path,
                    "biomarker_type": f.biomarker_type,
                    "severity": severity_str,
                    "function_name": f.function_name,
                    "line_start": f.line_start,
                    "line_end": f.line_end,
                    "details_json": json.dumps(f.details or {}),
                    "health_impact": float(f.health_impact),
                    "reason": f.reason or "",
                    "dimension": getattr(f, "dimension", None) or "defect",
                }
            else:
                data = dict(f)
                if "details" in data:
                    data["details_json"] = json.dumps(data.pop("details") or {})

            session.add(
                HealthFinding(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    **{
                        k: v
                        for k, v in data.items()
                        if k not in ("id", "repository_id") and hasattr(HealthFinding, k)
                    },
                )
            )
        await session.flush()


_GOVERNANCE_BIOMARKER_TYPES = frozenset(
    {"ungoverned_hotspot", "stale_governance", "contradictory_decision"}
)


async def replace_governance_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
) -> None:
    """Idempotent additive write of governance-layer health findings.

    Deletes any existing ``health_findings`` rows whose ``biomarker_type``
    is one of ``ungoverned_hotspot``, ``stale_governance``, or
    ``contradictory_decision`` for *repository_id*, then inserts the new
    *findings* in batches.

    This function deliberately does **not** recompute ``HealthFileMetric.score``
    — that pass has already completed in the upstream health-analysis phase.
    Governance findings surface through the findings layer (``get_risk``
    ``top_biomarkers``, ``get_context`` health block) rather than the numeric
    score.  A second score-recomputation pass would require re-loading the full
    per-file results table; the conservative choice is to leave scores as-is
    and let findings carry the governance signal.

    Composable with ``save_health_findings``: the delete is scoped to only
    the three governance biomarker types, so structural findings written by
    ``save_health_findings`` are untouched.

    Accepts ``HealthFindingData`` dataclasses or plain dicts (same protocol
    as ``save_health_findings``).
    """
    # Delete existing governance findings for this repo only.
    existing = await session.execute(
        select(HealthFinding).where(
            HealthFinding.repository_id == repository_id,
            HealthFinding.biomarker_type.in_(list(_GOVERNANCE_BIOMARKER_TYPES)),
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    if not findings:
        return

    for i in range(0, len(findings), _BATCH_SIZE):
        batch = findings[i : i + _BATCH_SIZE]
        for f in batch:
            if hasattr(f, "biomarker_type"):
                severity = f.severity
                severity_str = str(severity.value) if hasattr(severity, "value") else str(severity)
                data = {
                    "file_path": f.file_path,
                    "biomarker_type": f.biomarker_type,
                    "severity": severity_str,
                    "function_name": f.function_name,
                    "line_start": f.line_start,
                    "line_end": f.line_end,
                    "details_json": json.dumps(f.details or {}),
                    "health_impact": float(f.health_impact),
                    "reason": f.reason or "",
                    "dimension": getattr(f, "dimension", None) or "defect",
                }
            else:
                data = dict(f)
                if "details" in data:
                    data["details_json"] = json.dumps(data.pop("details") or {})

            session.add(
                HealthFinding(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    **{
                        k: v
                        for k, v in data.items()
                        if k not in ("id", "repository_id") and hasattr(HealthFinding, k)
                    },
                )
            )
        await session.flush()


async def save_health_metrics(
    session: AsyncSession,
    repository_id: str,
    metrics: list[Any],
) -> None:
    """Replace per-file health metrics for *repository_id*.

    Delete-then-insert (matches the findings writer). The unique
    constraint on (repository_id, file_path) means we cannot leave
    stale rows around without an upsert dance — delete-and-insert keeps
    it simple and aligns with how dead-code findings are written.
    """
    existing = await session.execute(
        select(HealthFileMetric).where(HealthFileMetric.repository_id == repository_id)
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    for i in range(0, len(metrics), _BATCH_SIZE):
        batch = metrics[i : i + _BATCH_SIZE]
        for m in batch:
            if hasattr(m, "file_path"):
                data = {
                    "file_path": m.file_path,
                    "score": float(m.score),
                    "max_ccn": int(m.max_ccn),
                    "max_nesting": int(m.max_nesting),
                    "nloc": int(m.nloc),
                    "duplication_pct": m.duplication_pct,
                    "has_test_file": bool(m.has_test_file),
                    "line_coverage_pct": m.line_coverage_pct,
                    "branch_coverage_pct": m.branch_coverage_pct,
                    "module": m.module,
                    "defect_score": getattr(m, "defect_score", None),
                    "maintainability_score": getattr(m, "maintainability_score", None),
                    "performance_score": getattr(m, "performance_score", None),
                }
            else:
                data = dict(m)

            session.add(
                HealthFileMetric(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    **{
                        k: v
                        for k, v in data.items()
                        if k not in ("id", "repository_id") and hasattr(HealthFileMetric, k)
                    },
                )
            )
        await session.flush()


async def get_health_findings(
    session: AsyncSession,
    repository_id: str,
    *,
    biomarker_type: str | None = None,
    min_severity: str | None = None,
    file_path: str | None = None,
    status: str = "open",
) -> list[HealthFinding]:
    q = select(HealthFinding).where(
        HealthFinding.repository_id == repository_id,
        HealthFinding.status == status,
    )
    if biomarker_type is not None:
        q = q.where(HealthFinding.biomarker_type == biomarker_type)
    if file_path is not None:
        q = q.where(HealthFinding.file_path == file_path)
    if min_severity is not None:
        # Severity order: low < medium < high < critical
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        threshold = order.get(min_severity, 0)
        allowed = [k for k, v in order.items() if v >= threshold]
        q = q.where(HealthFinding.severity.in_(allowed))
    q = q.order_by(HealthFinding.health_impact.desc())
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_health_metrics(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = None,
) -> list[HealthFileMetric]:
    q = select(HealthFileMetric).where(HealthFileMetric.repository_id == repository_id)
    if file_paths is not None:
        q = q.where(HealthFileMetric.file_path.in_(file_paths))
    q = q.order_by(HealthFileMetric.score.asc())
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_health_summary(session: AsyncSession, repository_id: str) -> dict:
    """Aggregate KPIs over the per-file metrics table."""
    metrics = await get_health_metrics(session, repository_id)
    if not metrics:
        return {
            "file_count": 0,
            "average_health": 10.0,
            "worst_performer_path": None,
            "worst_performer_score": None,
            "open_findings": 0,
        }
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    if total_nloc:
        avg = sum(m.score * max(m.nloc, 1) for m in metrics) / total_nloc
    else:
        avg = sum(m.score for m in metrics) / len(metrics)
    worst = min(metrics, key=lambda r: r.score)
    findings_count = await session.execute(
        select(func.count())
        .select_from(HealthFinding)
        .where(
            HealthFinding.repository_id == repository_id,
            HealthFinding.status == "open",
        )
    )
    return {
        "file_count": len(metrics),
        "average_health": round(avg, 2),
        "worst_performer_path": worst.file_path,
        "worst_performer_score": round(worst.score, 2),
        "open_findings": findings_count.scalar() or 0,
    }


async def update_health_finding_status(
    session: AsyncSession,
    finding_id: str,
    status: str,
) -> HealthFinding | None:
    f = await session.get(HealthFinding, finding_id)
    if f is None:
        return None
    f.status = status
    await session.flush()
    return f


# Rolling history kept per repo. Older snapshots are deleted on insert.
# 50 entries gives Phase 4's `--trend` flag (last 10) plus the 5-back
# Declining-Health baseline plenty of headroom.
HEALTH_SNAPSHOT_RETENTION: int = 50


async def save_health_snapshot(
    session: AsyncSession,
    repository_id: str,
    *,
    hotspot_health: float,
    average_health: float,
    worst_performer_path: str | None,
    worst_performer_score: float | None,
    per_file_scores: dict[str, float] | None = None,
    taken_at: datetime | None = None,
) -> HealthSnapshot:
    """Append a snapshot; prune oldest rows past ``HEALTH_SNAPSHOT_RETENTION``.

    Returns the inserted row. Per-file scores are stored compactly as
    ``{path: score}`` JSON (no per-finding detail — that lives in
    ``HealthFinding`` rows; snapshots are a thin history layer).
    """
    snap = HealthSnapshot(
        id=_new_uuid(),
        repository_id=repository_id,
        taken_at=taken_at or _now_utc(),
        hotspot_health=float(hotspot_health),
        average_health=float(average_health),
        worst_performer_path=worst_performer_path,
        worst_performer_score=(
            float(worst_performer_score) if worst_performer_score is not None else None
        ),
        per_file_scores_json=json.dumps(per_file_scores or {}, separators=(",", ":")),
    )
    session.add(snap)
    await session.flush()

    # Prune older-than-retention rows. We keep the *N* newest by
    # ``taken_at``; ties are broken by id (UUIDs are random but stable).
    rows = await session.execute(
        select(HealthSnapshot)
        .where(HealthSnapshot.repository_id == repository_id)
        .order_by(HealthSnapshot.taken_at.desc(), HealthSnapshot.id.desc())
    )
    history = list(rows.scalars().all())
    if len(history) > HEALTH_SNAPSHOT_RETENTION:
        for row in history[HEALTH_SNAPSHOT_RETENTION:]:
            await session.delete(row)
        await session.flush()
    return snap


async def list_health_snapshots(
    session: AsyncSession,
    repository_id: str,
    *,
    limit: int | None = None,
) -> list[HealthSnapshot]:
    """Return snapshots **oldest-first** (the shape ``trends.diff_snapshots``
    expects). Pass ``limit`` to cap the most recent N (still returned
    oldest-first for stable iteration)."""
    q = (
        select(HealthSnapshot)
        .where(HealthSnapshot.repository_id == repository_id)
        .order_by(HealthSnapshot.taken_at.asc(), HealthSnapshot.id.asc())
    )
    result = await session.execute(q)
    rows = list(result.scalars().all())
    if limit is not None and len(rows) > limit:
        rows = rows[-limit:]
    return rows


async def upsert_health_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
    *,
    file_paths: list[str],
) -> None:
    """Replace open findings **only for the given file paths**.

    Used by the incremental ``repowise update`` path so unchanged files
    keep their findings instead of being wiped on every partial re-index.
    """
    if not file_paths:
        return
    existing = await session.execute(
        select(HealthFinding).where(
            HealthFinding.repository_id == repository_id,
            HealthFinding.status == "open",
            HealthFinding.file_path.in_(file_paths),
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    for i in range(0, len(findings), _BATCH_SIZE):
        batch = findings[i : i + _BATCH_SIZE]
        for f in batch:
            if hasattr(f, "biomarker_type"):
                severity = f.severity
                severity_str = str(severity.value) if hasattr(severity, "value") else str(severity)
                data = {
                    "file_path": f.file_path,
                    "biomarker_type": f.biomarker_type,
                    "severity": severity_str,
                    "function_name": f.function_name,
                    "line_start": f.line_start,
                    "line_end": f.line_end,
                    "details_json": json.dumps(f.details or {}),
                    "health_impact": float(f.health_impact),
                    "reason": f.reason or "",
                    "dimension": getattr(f, "dimension", None) or "defect",
                }
            else:
                data = dict(f)
                if "details" in data:
                    data["details_json"] = json.dumps(data.pop("details") or {})

            session.add(
                HealthFinding(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    **{
                        k: v
                        for k, v in data.items()
                        if k not in ("id", "repository_id") and hasattr(HealthFinding, k)
                    },
                )
            )
        await session.flush()


async def upsert_health_metrics(
    session: AsyncSession,
    repository_id: str,
    metrics: list[Any],
) -> None:
    """Upsert per-file metrics; unchanged files in the table stay put.

    Sibling of ``save_health_metrics`` (which delete-then-inserts the
    whole repo). Used by the incremental analysis path so a partial
    re-index never wipes metric rows for files that weren't touched.
    """
    if not metrics:
        return
    paths = [m.file_path if hasattr(m, "file_path") else m["file_path"] for m in metrics]
    existing = await session.execute(
        select(HealthFileMetric).where(
            HealthFileMetric.repository_id == repository_id,
            HealthFileMetric.file_path.in_(paths),
        )
    )
    by_path = {row.file_path: row for row in existing.scalars().all()}

    for m in metrics:
        if hasattr(m, "file_path"):
            data = {
                "file_path": m.file_path,
                "score": float(m.score),
                "max_ccn": int(m.max_ccn),
                "max_nesting": int(m.max_nesting),
                "nloc": int(m.nloc),
                "duplication_pct": m.duplication_pct,
                "has_test_file": bool(m.has_test_file),
                "line_coverage_pct": m.line_coverage_pct,
                "branch_coverage_pct": m.branch_coverage_pct,
                "module": m.module,
                "defect_score": getattr(m, "defect_score", None),
                "maintainability_score": getattr(m, "maintainability_score", None),
                "performance_score": getattr(m, "performance_score", None),
            }
        else:
            data = dict(m)

        row = by_path.get(data["file_path"])
        if row is not None:
            for k, v in data.items():
                if k in ("id", "repository_id") or not hasattr(HealthFileMetric, k):
                    continue
                setattr(row, k, v)
        else:
            session.add(
                HealthFileMetric(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    **{
                        k: v
                        for k, v in data.items()
                        if k not in ("id", "repository_id") and hasattr(HealthFileMetric, k)
                    },
                )
            )
    await session.flush()


# ---------------------------------------------------------------------------
# Coverage CRUD
# ---------------------------------------------------------------------------


async def save_coverage_files(
    session: AsyncSession,
    repository_id: str,
    files: list[Any],
    *,
    source_format: str,
    ingested_commit_sha: str | None = None,
) -> None:
    """Replace coverage rows for *repository_id* with *files*.

    Mirrors the delete-then-insert pattern used by the health writers.
    *files* is a list of ``FileCoverage`` dataclasses (or dicts with the
    same shape).
    """
    existing = await session.execute(
        select(CoverageFile).where(CoverageFile.repository_id == repository_id)
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    for i in range(0, len(files), _BATCH_SIZE):
        batch = files[i : i + _BATCH_SIZE]
        for f in batch:
            if hasattr(f, "file_path"):
                data = {
                    "file_path": f.file_path,
                    "line_coverage_pct": float(f.line_coverage_pct),
                    "branch_coverage_pct": (
                        float(f.branch_coverage_pct) if f.branch_coverage_pct is not None else None
                    ),
                    "covered_lines_json": json.dumps(list(f.covered_lines or [])),
                    "total_coverable_lines": int(f.total_coverable_lines or 0),
                }
            else:
                data = dict(f)
                if "covered_lines" in data:
                    data["covered_lines_json"] = json.dumps(list(data.pop("covered_lines") or []))

            session.add(
                CoverageFile(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    source_format=source_format,
                    ingested_commit_sha=ingested_commit_sha,
                    **{
                        k: v
                        for k, v in data.items()
                        if k
                        not in (
                            "id",
                            "repository_id",
                            "source_format",
                            "ingested_commit_sha",
                        )
                        and hasattr(CoverageFile, k)
                    },
                )
            )
        await session.flush()


async def load_coverage_for_repo(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = None,
) -> list[CoverageFile]:
    q = select(CoverageFile).where(CoverageFile.repository_id == repository_id)
    if file_paths is not None:
        q = q.where(CoverageFile.file_path.in_(file_paths))
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_coverage_summary(
    session: AsyncSession,
    repository_id: str,
) -> dict[str, Any]:
    """Repo-level coverage aggregate. Returns an empty shape when no rows."""
    rows = await load_coverage_for_repo(session, repository_id)
    if not rows:
        return {
            "file_count": 0,
            "covered_lines": 0,
            "total_lines": 0,
            "line_coverage_pct": None,
            "branch_coverage_pct": None,
            "source_format": None,
            "ingested_at": None,
            "ingested_commit_sha": None,
        }
    covered = 0
    total = 0
    branch_pcts: list[float] = []
    branch_weights: list[int] = []
    for r in rows:
        covered += round(r.line_coverage_pct / 100.0 * r.total_coverable_lines)
        total += r.total_coverable_lines
        if r.branch_coverage_pct is not None:
            branch_pcts.append(r.branch_coverage_pct)
            branch_weights.append(max(r.total_coverable_lines, 1))
    line_pct = (covered / total * 100.0) if total else 0.0
    branch_pct: float | None
    if branch_pcts:
        wsum = sum(branch_weights)
        branch_pct = sum(p * w for p, w in zip(branch_pcts, branch_weights, strict=True)) / wsum
    else:
        branch_pct = None
    latest = max(rows, key=lambda r: r.ingested_at)
    return {
        "file_count": len(rows),
        "covered_lines": covered,
        "total_lines": total,
        "line_coverage_pct": round(line_pct, 2),
        "branch_coverage_pct": round(branch_pct, 2) if branch_pct is not None else None,
        "source_format": latest.source_format,
        "ingested_at": latest.ingested_at,
        "ingested_commit_sha": latest.ingested_commit_sha,
    }
