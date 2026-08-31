"""CRUD operations for code-health findings, metrics, and snapshots
(repowise persistence layer)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from ....analysis.health.perf.coverage import PerfCoverage

from ....analysis.health.finding_identity import finding_public_id

# The comparator is pure and lives with the health read models; only the SQL
# that feeds it belongs here. Re-imported rather than moved out of reach: the
# crud surface is where the MCP tool and the tests already read it from.
from ....analysis.health.ranking import (
    deduction_by_path,
    sort_metrics_worst_first,
    worst_metric,
)
from ....analysis.health.rows import detail_map
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


def _health_finding_row_kwargs(finding: Any, repository_id: str) -> dict:
    """Normalize an analyzer dataclass or a plain dict into ORM row kwargs.

    All three writers land here, so a column added to the row is written by
    every path or by none. ``public_id`` is stamped here because this is the
    one place every persisted finding passes through.
    """
    if hasattr(finding, "biomarker_type"):
        severity = finding.severity
        data = {
            "file_path": finding.file_path,
            "biomarker_type": finding.biomarker_type,
            "severity": str(severity.value) if hasattr(severity, "value") else str(severity),
            "function_name": finding.function_name,
            "line_start": finding.line_start,
            "line_end": finding.line_end,
            "details_json": json.dumps(finding.details or {}),
            "health_impact": float(finding.health_impact),
            "reason": finding.reason or "",
            "dimension": getattr(finding, "dimension", None) or "defect",
        }
    else:
        data = dict(finding)
        if "details" in data:
            data["details_json"] = json.dumps(data.pop("details") or {})
    data.setdefault("public_id", finding_public_id(finding))
    if data.get("dimension") == "performance":
        # Present before the finalizer runs because the analyzer stamps it into
        # ``details``; the finalizer restates it from authoritative stored rows.
        data.setdefault("opportunity_id", detail_map(finding).get("opportunity_id"))
    return {
        "id": _new_uuid(),
        "repository_id": repository_id,
        **{
            k: v
            for k, v in data.items()
            if k not in ("id", "repository_id") and hasattr(HealthFinding, k)
        },
    }


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
            session.add(HealthFinding(**_health_finding_row_kwargs(f, repository_id)))
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
            session.add(HealthFinding(**_health_finding_row_kwargs(f, repository_id)))
        await session.flush()


async def save_health_metrics(
    session: AsyncSession,
    repository_id: str,
    metrics: list[Any],
    *,
    analyzed_commit: str | None = None,
) -> None:
    """Replace per-file health metrics for *repository_id*.

    Delete-then-insert (matches the findings writer). The unique
    constraint on (repository_id, file_path) means we cannot leave
    stale rows around without an upsert dance — delete-and-insert keeps
    it simple and aligns with how dead-code findings are written.

    ``analyzed_commit`` stamps every written row with the commit these scores
    were computed against — the same pattern as ``save_coverage_files``'
    ``ingested_commit_sha``. Health runs as its own pass and can lag the index,
    so ``Repository.head_commit`` cannot answer "how old is this score". Omitted
    → NULL, which reads as "not recorded" rather than "current".
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
            # After the dict-passthrough branch so an explicit per-row value in a
            # raw dict still wins; the sha is a property of the pass, not the row.
            if analyzed_commit is not None:
                data.setdefault("analyzed_commit", analyzed_commit)

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


async def backfill_module_attribution(
    session: AsyncSession,
    repository_id: str,
    package_roots: set[str],
) -> int:
    """Recompute every metric row's ``module`` from the repo layout.

    Returns the number of rows changed, so a caller can stay silent when there
    was nothing to correct.

    ``module`` is a pure function of ``(file_path, package_roots)`` — no parse,
    no symbols, no git, no model — so correcting it never needs the rows
    rewritten by a re-score, let alone a re-index. That is the whole point:
    a directory label must not cost users an indexing run.

    Idempotent by construction, and it converges with the indexer rather than
    fighting it: both call the same :func:`module_for` over the same
    disk-scanned roots, so a full index writing a *different* value would mean
    the two disagree about the repo layout, and running this again would flip
    it back rather than settle.
    """
    from ....ingestion.package_roots import module_for

    result = await session.execute(
        select(HealthFileMetric).where(HealthFileMetric.repository_id == repository_id)
    )
    changed = 0
    for row in result.scalars().all():
        expected = module_for(row.file_path, package_roots)
        if row.module != expected:
            row.module = expected
            changed += 1
    if changed:
        await session.flush()
    return changed


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
    exclude_dimensions: tuple[str, ...] | None = None,
    status: str = "open",
) -> list[HealthFinding]:
    """Open findings for one repository, ordered by health impact.

    ``exclude_dimensions`` is how a general queue keeps a dimension out of a
    ranking it does not share units with. It is ignored when ``dimension``
    names one explicitly, so asking for a dimension always returns it.
    """
    q = select(HealthFinding).where(
        HealthFinding.repository_id == repository_id,
        HealthFinding.status == status,
    )
    if dimension is None and exclude_dimensions:
        q = q.where(
            or_(
                HealthFinding.dimension.is_(None),
                HealthFinding.dimension.not_in(list(exclude_dimensions)),
            )
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


async def get_deduction_by_path(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = None,
    status: str = "open",
) -> dict[str, float]:
    """``{file_path: summed health_impact}`` over the repo's findings.

    The pre-clamp deduction magnitude, aggregated in SQL rather than by
    hydrating every finding. Each stored ``health_impact`` is already the
    applied (capped) value, so the sum equals the breakdown endpoint's
    ``total_deduction``.

    Files with no findings are simply absent — a caller reading this as a
    ranking key should treat a miss as 0.0 (a clean file has no magnitude).
    """
    totals: dict[str, float] = {}
    base = select(
        HealthFinding.file_path,
        func.sum(HealthFinding.health_impact),
    ).where(
        HealthFinding.repository_id == repository_id,
        HealthFinding.status == status,
    )
    # Batched like the other ``IN`` lookups here: SQLITE_MAX_VARIABLE_NUMBER is
    # 999 before SQLite 3.32 and a module-scoped caller expands without bound.
    chunks: list[list[str] | None] = (
        [file_paths[i : i + _BATCH_SIZE] for i in range(0, len(file_paths), _BATCH_SIZE)]
        if file_paths is not None
        else [None]
    )
    for chunk in chunks:
        q = base if chunk is None else base.where(HealthFinding.file_path.in_(chunk))
        for path, total in (await session.execute(q.group_by(HealthFinding.file_path))).all():
            totals[path] = float(total or 0.0)
    return totals


async def get_health_metrics(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = None,
) -> list[HealthFileMetric]:
    """Per-file health metrics, ordered worst-first.

    See :func:`sort_metrics_worst_first` for why the order is not a plain
    ``ORDER BY score``.
    """
    q = select(HealthFileMetric).where(HealthFileMetric.repository_id == repository_id)
    if file_paths is not None:
        q = q.where(HealthFileMetric.file_path.in_(file_paths))
    result = await session.execute(q)
    rows = _filter_excluded_paths(
        list(result.scalars().all()),
        await _health_exclude_spec(session, repository_id),
    )
    if len(rows) < 2:
        # Nothing to order. Skips the aggregate for the single-file lookups
        # (symbol drawer, file detail, one-plan refactoring view), which are
        # per-request reads that would otherwise pay for a ranking they cannot
        # use.
        return rows
    # Scoped to the caller's paths, not to ``rows``: a repo-wide read wants the
    # single grouped scan, and the extra keys an exclude config leaves in the
    # map are never looked up.
    return sort_metrics_worst_first(
        rows,
        await get_deduction_by_path(session, repository_id, file_paths=file_paths),
    )


async def get_average_health(session: AsyncSession, repository_id: str) -> float | None:
    """The repo's NLOC-weighted average health score, or ``None`` if unmeasured.

    Numerically identical to ``get_health_summary()["average_health"]`` — same
    weighting, same ``max(nloc, 1)`` floor, same 2dp rounding — for callers that
    want *only* that number. The summary computes it as a by-product of building
    twenty-odd KPIs, which costs a full ORM hydration of the metrics table, the
    grouped deduction aggregate behind the worst-first ranking, the whole
    findings table for the per-dimension counts, and the graph's language map
    for perf coverage. The public badge endpoints want one float.

    Three columns and the same exclusion filter every other health read applies.
    That filter is a compiled ``pathspec``, not something SQL can express, which
    is why this is a narrow select reduced in Python rather than an ``AVG()``:
    a SQL average would quietly include files the dashboard excludes, and the
    badge would then disagree with the page it links to.
    """
    rows = _filter_excluded_paths(
        list(
            (
                await session.execute(
                    select(
                        HealthFileMetric.file_path,
                        HealthFileMetric.score,
                        HealthFileMetric.nloc,
                    ).where(HealthFileMetric.repository_id == repository_id)
                )
            ).all()
        ),
        await _health_exclude_spec(session, repository_id),
    )
    if not rows:
        return None
    total_nloc = sum(max(r.nloc, 1) for r in rows)
    if total_nloc:
        avg = sum(r.score * max(r.nloc, 1) for r in rows) / total_nloc
    else:
        avg = sum(r.score for r in rows) / len(rows)
    return round(avg, 2)


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


async def get_health_summary(
    session: AsyncSession,
    repository_id: str,
    *,
    metrics: list | None = None,
    findings: list | None = None,
) -> dict:
    """Aggregate KPIs over the per-file metrics table.

    *metrics* and *findings* let a caller that has already loaded those rows
    hand them over instead of paying for a second full read. Callers doing more
    than one thing with them — the overview payload builds these KPIs, the
    severity breakdown and the defect-accuracy stat off the same two tables —
    would otherwise pull each table twice per request.

    Both are expected to be exactly what this function would have read itself:
    ``get_health_metrics(repository_id)`` and
    ``get_health_findings(repository_id)``, i.e. every open row for the repo,
    post-exclusion. Handing over a *filtered* subset would silently skew
    ``open_findings`` and the per-dimension counts, which are repo headlines.
    """
    if metrics is None:
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

    if findings is None:
        findings = await get_health_findings(session, repository_id)

    # Ranked with the same key as ``get_health_metrics``, off the findings this
    # function already holds. Previously a plain ``min`` on the score, which
    # agreed with the worst-files list only because every caller happened to
    # pass an already-ranked list — a floor tie made the headline and the list
    # under it disagree the moment one did not.
    worst = worst_metric(metrics, deduction_by_path(findings))

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

#: How many snapshots a *single file's* trend line reads. Every snapshot row
#: carries two whole-repo ``{path: float}`` maps, so a route that wants one
#: path's score series pays the entire retention window in TEXT to extract a
#: handful of floats. Twenty points is more than the decline heuristics need
#: (``DECLINE_LOOKBACK`` is 5) and more than the sparkline can usefully draw.
#:
#: One knock-on: ``FileTrend.snapshot_count`` is documented as the size of the
#: whole repo window, and on the two routes that pass this it becomes the size
#: of *this* window instead. Nothing reads that field off a per-file trend
#: today — the dashboard and the trend view read it off the repo-level routes,
#: which are unlimited and unchanged. Projecting one path's series in SQL would
#: keep the count true and drop the bytes further, but no query in this module
#: extracts from a JSON column and doing that portably across SQLite and
#: PostgreSQL is its own change.
FILE_TREND_SNAPSHOT_WINDOW: int = 20


async def save_health_snapshot(
    session: AsyncSession,
    repository_id: str,
    *,
    hotspot_health: float,
    average_health: float,
    worst_performer_path: str | None,
    worst_performer_score: float | None,
    per_file_scores: dict[str, float] | None = None,
    per_file_deductions: dict[str, float] | None = None,
    taken_at: datetime | None = None,
) -> HealthSnapshot:
    """Append a snapshot; prune oldest rows past ``HEALTH_SNAPSHOT_RETENTION``.

    Returns the inserted row. Per-file scores are stored compactly as
    ``{path: score}`` JSON (no per-finding detail — that lives in
    ``HealthFinding`` rows; snapshots are a thin history layer).

    ``per_file_deductions`` is the same shape for the files whose score is held
    at the floor, where the stored score has stopped carrying information.
    Build both with ``trends.snapshot_file_maps`` rather than by hand: a repo
    whose writers disagree gets a history that changes depth depending on which
    command last wrote it.
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
        per_file_deductions_json=json.dumps(per_file_deductions or {}, separators=(",", ":")),
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
    oldest-first for stable iteration).

    ``limit`` is pushed into SQL rather than applied to the fetched list. Every
    row carries ``per_file_scores_json`` and ``per_file_deductions_json``, both
    whole-repo ``{path: float}`` maps, and the table keeps
    ``HEALTH_SNAPSHOT_RETENTION`` (50) of them — so trimming in Python read the
    entire history off disk to throw most of it away. Taking the newest N
    descending and reversing gives the same list for a fraction of the bytes.
    """
    if limit is not None:
        newest_first = await session.execute(
            select(HealthSnapshot)
            .where(HealthSnapshot.repository_id == repository_id)
            .order_by(HealthSnapshot.taken_at.desc(), HealthSnapshot.id.desc())
            .limit(limit)
        )
        return list(reversed(newest_first.scalars().all()))
    q = (
        select(HealthSnapshot)
        .where(HealthSnapshot.repository_id == repository_id)
        .order_by(HealthSnapshot.taken_at.asc(), HealthSnapshot.id.asc())
    )
    result = await session.execute(q)
    return list(result.scalars().all())


class HealthSnapshotScalars(NamedTuple):
    """One snapshot's numeric columns, with no per-file map behind them."""

    taken_at: datetime | None
    average_health: float
    hotspot_health: float


class HealthSnapshotHeadline(NamedTuple):
    """The snapshot figures a dashboard header actually reads.

    ``recent`` is empty unless the caller asked for it, and is ordered
    **oldest-first** to match ``list_health_snapshots`` — a trend line reads
    left to right, and the newest row is ``recent[-1]``.
    """

    hotspot_health: float | None
    taken_at: datetime | None
    snapshot_count: int
    recent: tuple[HealthSnapshotScalars, ...] = ()


async def get_health_snapshot_headline(
    session: AsyncSession,
    repository_id: str,
    *,
    recent: int = 0,
) -> HealthSnapshotHeadline:
    """Latest snapshot's ``hotspot_health`` / ``taken_at``, plus how many exist.

    ``list_health_snapshots`` hydrates whole entities, and every row carries a
    ``per_file_scores_json`` blob sized by the repo's file count — on this
    codebase that is ~186 KB each, 2.8 MB across the retained history and ~9 MB
    at ``HEALTH_SNAPSHOT_RETENTION``. A caller that only needs scalars was
    reading all of it to use a handful of floats.

    Pass ``recent=N`` for the newest N rows' scalars as well — a sparkline and a
    delta-vs-previous both want a bounded window of numbers, not the history.
    ``snapshot_count`` is always the **true** total, independent of ``recent``,
    so a capped window can never be mistaken for the whole history.

    No JSON column is touched. Callers that genuinely need the per-file maps
    (the trend routes, the file drawer's sparkline) should keep using
    ``list_health_snapshots``; a caller that needs only their *sizes* wants
    ``get_health_snapshot_file_counts``.
    """
    rows = (
        await session.execute(
            select(
                HealthSnapshot.hotspot_health,
                HealthSnapshot.taken_at,
                HealthSnapshot.average_health,
            )
            .where(HealthSnapshot.repository_id == repository_id)
            .order_by(HealthSnapshot.taken_at.asc(), HealthSnapshot.id.asc())
        )
    ).all()
    if not rows:
        return HealthSnapshotHeadline(None, None, 0)
    hotspot_health, taken_at, _ = rows[-1]
    window = rows[-recent:] if recent > 0 else []
    return HealthSnapshotHeadline(
        float(hotspot_health),
        taken_at,
        len(rows),
        tuple(
            HealthSnapshotScalars(r_taken_at, float(r_avg), float(r_hotspot))
            for r_hotspot, r_taken_at, r_avg in window
        ),
    )


async def get_health_snapshot_file_counts(
    session: AsyncSession,
    repository_id: str,
    *,
    limit: int = 2,
) -> list[int]:
    """How many files the newest *limit* snapshots scored, oldest-first.

    The only thing the overview's ``file_count`` delta wants out of
    ``per_file_scores_json`` is its key count, and it compares two rows. Loading
    the whole retained history to measure two of its maps read 2.8 MB to use
    375 KB of it.

    Ordering matches ``list_health_snapshots`` exactly, so "the newest two"
    cannot mean different rows here than there.

    Anything that is not a ``{path: score}`` object counts 0 — unparseable, but
    also a bare JSON array or string, which ``len()`` would happily measure into
    a file count meaning nothing. The caller reads 0 as "unknown" and omits the
    delta rather than publishing a number it cannot justify. This is deliberately
    stricter than the ``len(json.loads(...))`` it replaced;
    ``save_health_snapshot`` only ever writes an object, so it can only bite on
    rows that did not come from it.
    """
    if limit <= 0:
        return []
    rows = (
        await session.execute(
            select(HealthSnapshot.per_file_scores_json)
            .where(HealthSnapshot.repository_id == repository_id)
            .order_by(HealthSnapshot.taken_at.desc(), HealthSnapshot.id.desc())
            .limit(limit)
        )
    ).all()
    counts: list[int] = []
    for (blob,) in reversed(rows):
        try:
            parsed = json.loads(blob or "{}")
        except (TypeError, ValueError):
            counts.append(0)
            continue
        counts.append(len(parsed) if isinstance(parsed, dict) else 0)
    return counts


async def upsert_health_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
    *,
    file_paths: list[str],
    dimension: str | None = None,
) -> None:
    """Replace open findings **only for the given file paths**.

    Used by the incremental ``repowise update`` path so unchanged files
    keep their findings instead of being wiped on every partial re-index.
    """
    if not file_paths:
        return
    predicates = [
        HealthFinding.repository_id == repository_id,
        HealthFinding.status == "open",
        HealthFinding.file_path.in_(file_paths),
    ]
    if dimension is not None:
        predicates.append(HealthFinding.dimension == dimension)
    existing = await session.execute(select(HealthFinding).where(*predicates))
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    allowed = set(file_paths)
    scoped = [
        finding
        for finding in findings
        if (finding.file_path if hasattr(finding, "file_path") else finding.get("file_path"))
        in allowed
        and (
            dimension is None
            or (
                getattr(finding, "dimension", None)
                if hasattr(finding, "dimension")
                else finding.get("dimension")
            )
            == dimension
        )
    ]
    for i in range(0, len(scoped), _BATCH_SIZE):
        batch = scoped[i : i + _BATCH_SIZE]
        for f in batch:
            session.add(HealthFinding(**_health_finding_row_kwargs(f, repository_id)))
        await session.flush()


async def upsert_health_metrics(
    session: AsyncSession,
    repository_id: str,
    metrics: list[Any],
    *,
    analyzed_commit: str | None = None,
) -> None:
    """Upsert per-file metrics; unchanged files in the table stay put.

    Sibling of ``save_health_metrics`` (which delete-then-inserts the
    whole repo). Used by the incremental analysis path so a partial
    re-index never wipes metric rows for files that weren't touched.

    ``analyzed_commit`` stamps only the rows this call rewrites, which is the
    point of recording it per row: after a partial pass the table honestly
    reports two commits, and ``get_health``'s ``_meta`` says so rather than
    claiming one scoring commit for the whole repo.
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
        # Only when the caller knows the sha. An unconditional set would push a
        # None over a sha already on the row, so a caller that simply does not
        # track the commit would erase the stamp a caller that does had written.
        if analyzed_commit is not None:
            data.setdefault("analyzed_commit", analyzed_commit)

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
