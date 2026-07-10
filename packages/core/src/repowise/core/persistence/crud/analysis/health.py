"""CRUD operations for code-health findings, metrics, and snapshots
(repowise persistence layer)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ....analysis.health.perf.coverage import PerfCoverage

from ...models import (
    GraphNode,
    HealthFileMetric,
    HealthFinding,
    HealthSnapshot,
    Repository,
    _new_uuid,
    _now_utc,
)
from .._shared import _BATCH_SIZE


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


async def _health_exclude_spec(session: AsyncSession, repository_id: str) -> Any:
    repo = await session.get(Repository, repository_id)
    if repo is None:
        return None

    patterns: list[str] = []
    seen: set[str] = set()

    def _add(values: Any) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if isinstance(value, str) and value not in seen:
                seen.add(value)
                patterns.append(value)

    try:
        settings = json.loads(getattr(repo, "settings_json", "") or "{}")
        if isinstance(settings, dict):
            _add(settings.get("exclude_patterns"))
    except (TypeError, ValueError):
        pass

    try:
        from repowise.core.repo_config import load_repo_config

        cfg = load_repo_config(Path(repo.local_path))
        if isinstance(cfg, dict):
            _add(cfg.get("exclude_patterns"))
    except Exception:
        pass

    if not patterns:
        return None

    import pathspec

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def _filter_excluded_paths(rows: list[Any], spec: Any) -> list[Any]:
    if spec is None:
        return rows
    return [row for row in rows if not spec.match_file(getattr(row, "file_path", ""))]


async def get_health_findings(
    session: AsyncSession,
    repository_id: str,
    *,
    biomarker_type: str | None = None,
    min_severity: str | None = None,
    file_path: str | None = None,
    dimension: str | None = None,
    status: str = "open",
) -> list[HealthFinding]:
    q = select(HealthFinding).where(
        HealthFinding.repository_id == repository_id,
        HealthFinding.status == status,
    )
    if biomarker_type is not None:
        # Accept a comma-separated list so a caller can pull several biomarker
        # types in one request (e.g. the function-level + coupling panels).
        # A single value with no comma still matches exactly (``IN`` of one).
        types = [t.strip() for t in biomarker_type.split(",") if t.strip()]
        if types:
            q = q.where(HealthFinding.biomarker_type.in_(types))
    if file_path is not None:
        q = q.where(HealthFinding.file_path == file_path)
    if dimension is not None:
        # Older rows predate the split and carry a NULL dimension that homes
        # under "defect"; fold those in so a defect filter never drops them.
        if dimension == "defect":
            q = q.where(or_(HealthFinding.dimension == "defect", HealthFinding.dimension.is_(None)))
        else:
            q = q.where(HealthFinding.dimension == dimension)
    if min_severity is not None:
        # Severity order: low < medium < high < critical
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        threshold = order.get(min_severity, 0)
        allowed = [k for k, v in order.items() if v >= threshold]
        q = q.where(HealthFinding.severity.in_(allowed))
    q = q.order_by(HealthFinding.health_impact.desc())
    result = await session.execute(q)
    return _filter_excluded_paths(
        list(result.scalars().all()),
        await _health_exclude_spec(session, repository_id),
    )


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
    return _filter_excluded_paths(
        list(result.scalars().all()),
        await _health_exclude_spec(session, repository_id),
    )


async def get_file_language_map(session: AsyncSession, repository_id: str) -> dict[str, str]:
    """``{file_path: language_tag}`` for every file node in the graph."""
    q = select(GraphNode.node_id, GraphNode.language).where(
        GraphNode.repository_id == repository_id,
        GraphNode.node_type == "file",
    )
    return {node_id: language for node_id, language in (await session.execute(q)).all()}


async def get_perf_coverage(session: AsyncSession, repository_id: str) -> PerfCoverage:
    """How much of the analyzed code the performance pass was able to run on."""
    # Imported lazily to keep the persistence layer free of an analysis-layer
    # import at module load (and to avoid a circular import).
    from ....analysis.health.perf.coverage import coverage_for_metrics

    metrics = await get_health_metrics(session, repository_id)
    lang_by_path = await get_file_language_map(session, repository_id)
    return coverage_for_metrics(metrics, lang_by_path)


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
            "maintainability_average": None,
            "performance_average": None,
            "maintainability_findings": 0,
            "performance_findings": 0,
            "performance_findings_density": None,
            "performance_coverage_pct": None,
            "performance_covered_files": 0,
            "performance_analyzed_files": 0,
            "performance_skipped_files": 0,
            "performance_unsupported_languages": [],
            "worst_performance_path": None,
            "worst_performance_score": None,
        }
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    if total_nloc:
        avg = sum(m.score * max(m.nloc, 1) for m in metrics) / total_nloc
    else:
        avg = sum(m.score for m in metrics) / len(metrics)
    worst = min(metrics, key=lambda r: r.score)

    # Maintainability headline: NLOC-weighted average over the per-file
    # maintainability scores (skipping rows that predate the split / lack one).
    # ``None`` when no row carries a maintainability score so the surface reads
    # "not measured" rather than a misleading 10.0.
    maint_scored = [m for m in metrics if getattr(m, "maintainability_score", None) is not None]
    maintainability_average: float | None = None
    if maint_scored:
        maint_nloc = sum(max(m.nloc, 1) for m in maint_scored)
        if maint_nloc:
            maintainability_average = (
                sum(m.maintainability_score * max(m.nloc, 1) for m in maint_scored) / maint_nloc
            )
        else:
            maintainability_average = sum(m.maintainability_score for m in maint_scored) / len(
                maint_scored
            )

    # Performance headline: same NLOC-weighted average over the per-file
    # performance scores (static performance RISK). ``None`` when no row carries
    # a performance score so the surface reads "not measured" rather than 10.0.
    perf_scored = [m for m in metrics if getattr(m, "performance_score", None) is not None]
    performance_average: float | None = None
    if perf_scored:
        perf_nloc = sum(max(m.nloc, 1) for m in perf_scored)
        if perf_nloc:
            performance_average = (
                sum(m.performance_score * max(m.nloc, 1) for m in perf_scored) / perf_nloc
            )
        else:
            performance_average = sum(m.performance_score for m in perf_scored) / len(perf_scored)

    # Worst-performance file: the lowest per-file performance score, surfaced only
    # when there is genuine risk (score < 10) so a clean repo shows no actionable
    # target rather than a misleading "worst" at a perfect 10.0.
    worst_performance_path: str | None = None
    worst_performance_score: float | None = None
    if perf_scored:
        perf_worst = min(perf_scored, key=lambda r: r.performance_score)
        if perf_worst.performance_score < 10.0:
            worst_performance_path = perf_worst.file_path
            worst_performance_score = round(perf_worst.performance_score, 2)

    findings = await get_health_findings(session, repository_id)
    by_dim: dict[str, int] = {}
    for finding in findings:
        dim = finding.dimension or "defect"
        by_dim[dim] = by_dim.get(dim, 0) + 1

    # Perf coverage: honest denominator for the score. On a repo that is mostly a
    # perf-unsupported language the aggregate perf average is meaningless, so we
    # surface how much of the analyzed code a detector actually ran on, plus a
    # findings-per-10K-LOC density over the *covered* lines (not the whole repo).
    from ....analysis.health.perf.coverage import coverage_for_metrics

    lang_by_path = await get_file_language_map(session, repository_id)
    coverage = coverage_for_metrics(metrics, lang_by_path)
    performance_findings = by_dim.get("performance", 0)
    performance_findings_density: float | None = None
    if coverage.covered_nloc > 0:
        performance_findings_density = round(
            10000.0 * performance_findings / coverage.covered_nloc, 2
        )
    return {
        "file_count": len(metrics),
        "average_health": round(avg, 2),
        "worst_performer_path": worst.file_path,
        "worst_performer_score": round(worst.score, 2),
        "open_findings": len(findings),
        "maintainability_average": (
            round(maintainability_average, 2) if maintainability_average is not None else None
        ),
        "performance_average": (
            round(performance_average, 2) if performance_average is not None else None
        ),
        "maintainability_findings": by_dim.get("maintainability", 0),
        "performance_findings": performance_findings,
        "performance_findings_density": performance_findings_density,
        "performance_coverage_pct": (coverage.pct_loc if coverage.analyzed_files else None),
        "performance_covered_files": coverage.covered_files,
        "performance_analyzed_files": coverage.analyzed_files,
        "performance_skipped_files": coverage.skipped_files,
        "performance_unsupported_languages": coverage.unsupported_languages,
        "worst_performance_path": worst_performance_path,
        "worst_performance_score": worst_performance_score,
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
