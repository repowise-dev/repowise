"""Repository-level health KPIs: the badge figure and the dashboard summary."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .....analysis.health.grading import analysed_share
from .....analysis.health.ranking import deduction_by_path, worst_kpis
from .....analysis.health.rows import split_unscored
from .....analysis.health.scoring import nloc_weighted_attr
from ....models import HealthFileMetric, HealthFinding
from ..plain_rows import plain_rows
from ..shown import shown
from .exclude import _filter_excluded_paths, health_exclude_spec
from .findings import open_finding_count
from .metrics import get_file_language_map, get_health_metrics


def _rounded(value: float | None) -> float | None:
    """Two-decimal wire value, passing ``None`` through as "not measured"."""
    return round(value, 2) if value is not None else None


async def get_headline_health(
    session: AsyncSession, repository_id: str
) -> tuple[float | None, float | None]:
    """``(average health, analysed share)``: the badge's figure and its band gate.

    The average is ``None`` if unmeasured; the share is ``analysed_share``.

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
        await health_exclude_spec(session, repository_id),
    )
    # Files with no score (no health dialect for their language) are skipped,
    # so a repository of nothing else reads unmeasured, never 10.0.
    scored, unanalysed = split_unscored(rows)
    return _rounded(nloc_weighted_attr(scored, "score")), analysed_share(len(scored), unanalysed)


async def _open_finding_totals_rows(session: AsyncSession, repository_id: str) -> list[Any]:
    """The open findings :func:`get_health_findings` returns, narrowed to the
    columns the summary totals read.

    The summary sums and counts these; it never reads their tier, so it skips
    the details column and the tiering pass over every open row. Impact order
    is deliberate: per-file deductions are summed in the order the MCP
    dashboard sums them, so both name the same worst file; ``id`` makes the
    order total.
    """
    rows = await session.execute(
        select(HealthFinding.file_path, HealthFinding.health_impact, HealthFinding.dimension)
        .where(
            HealthFinding.repository_id == repository_id,
            HealthFinding.status == "open",
            # The totals sum what the scores counted, file-held rows included.
            shown(
                "health", HealthFinding.biomarker_type, HealthFinding.file_path, file_holds=False
            ),
        )
        .order_by(HealthFinding.health_impact.desc(), HealthFinding.id)
    )
    return _filter_excluded_paths(
        plain_rows(rows), await health_exclude_spec(session, repository_id)
    )


async def get_health_summary(
    session: AsyncSession,
    repository_id: str,
    *,
    metrics: list | None = None,
    findings: list | None = None,
    include_unverified: bool = False,
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
    ``performance_causes`` counts language-gated causes only with *include_unverified*.
    """
    if metrics is None:
        metrics = await get_health_metrics(session, repository_id)
    # A file in a language health has no dialect for is stored unscored. It is
    # counted apart and left out of every figure below.
    metrics, unanalysed = split_unscored(metrics)
    if not metrics:
        return {
            "file_count": 0,
            "unanalysed_file_count": unanalysed,
            "analysed_share": analysed_share(0, unanalysed),
            "average_health": None,
            "worst_performer_path": None,
            "worst_performer_score": None,
            "worst_test_path": None,
            "worst_test_score": None,
            "open_findings": 0,
            "maintainability_average": None,
            "performance_average": None,
            "structure_average": None,
            "history_average": None,
            "maintainability_findings": 0,
            "performance_causes": None,
            "performance_coverage_pct": None,
            "performance_covered_files": 0,
            "performance_analyzed_files": 0,
            "performance_skipped_files": 0,
            "performance_unsupported_languages": [],
            "worst_performance_path": None,
            "worst_performance_score": None,
        }
    avg = nloc_weighted_attr(metrics, "score")

    # The two co-surfaced pillars: the same NLOC weighting over their own
    # columns, ``None`` when no row carries one so a surface reads "not
    # measured" rather than a misleading 10.0.
    maintainability_average = nloc_weighted_attr(metrics, "maintainability_score")
    performance_average = nloc_weighted_attr(metrics, "performance_score")
    perf_scored = [m for m in metrics if getattr(m, "performance_score", None) is not None]

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
        findings = await _open_finding_totals_rows(session, repository_id)

    # Ranked with the same key as ``get_health_metrics``, off the findings this
    # function already holds, and in Fix first's scope: the file ``get_health``
    # lists first, so a script or schema scoring lowest is not named. The worst
    # test is named apart, so a weak test is not lost behind it.
    worst = worst_kpis(metrics, deduction_by_path(findings))

    by_dim: dict[str, int] = {}
    for finding in findings:
        dim = finding.dimension or "defect"
        by_dim[dim] = by_dim.get(dim, 0) + 1

    # Perf coverage: honest denominator for the score. On a repo that is mostly a
    # perf-unsupported language the aggregate perf average is meaningless, so we
    # surface how much of the analyzed code a detector actually ran on.
    from .....analysis.health.perf.coverage import coverage_for_metrics
    from ..performance import performance_headline

    lang_by_path = await get_file_language_map(session, repository_id)
    coverage = coverage_for_metrics(metrics, lang_by_path)
    causes = await performance_headline(
        session,
        repository_id,
        covered_nloc=coverage.covered_nloc,
        include_unverified=include_unverified,
    )
    return {
        "file_count": len(metrics),
        "unanalysed_file_count": unanalysed,
        "analysed_share": analysed_share(len(metrics), unanalysed),
        "average_health": _rounded(avg),
        **worst,
        "open_findings": open_finding_count(findings),
        "maintainability_average": _rounded(maintainability_average),
        "performance_average": _rounded(performance_average),
        # The headline's two halves, in deduction points. Both come off columns
        # already on the rows in hand, so this costs no query — and without them
        # the page can show a score falling without saying which half moved.
        "structure_average": _rounded(nloc_weighted_attr(metrics, "structure_deduction")),
        "history_average": _rounded(nloc_weighted_attr(metrics, "history_deduction")),
        "maintainability_findings": by_dim.get("maintainability", 0),
        "performance_causes": causes.as_dict(),
        "performance_coverage_pct": (coverage.pct_loc if coverage.analyzed_files else None),
        "performance_covered_files": coverage.covered_files,
        "performance_analyzed_files": coverage.analyzed_files,
        "performance_skipped_files": coverage.skipped_files,
        "performance_unsupported_languages": coverage.unsupported_languages,
        "worst_performance_path": worst_performance_path,
        "worst_performance_score": worst_performance_score,
    }
