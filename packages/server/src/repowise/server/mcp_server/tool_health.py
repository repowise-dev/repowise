"""MCP tool: get_health — code-health markers and per-file scores."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.health.churn_complexity import churn_complexity_points
from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
from repowise.core.analysis.health.grading import HEALTHY_MIN, band_for
from repowise.core.analysis.health.grading import distribution as health_distribution
from repowise.core.analysis.health.perf.coverage import PerfCoverage, coverage_for_metrics
from repowise.core.analysis.health.signals import file_signals
from repowise.core.analysis.health.suggestions import suggestion_for
from repowise.core.analysis.health.trends import diff_snapshots, file_trend, recent_kpis
from repowise.core.persistence.crud import (
    get_all_git_metadata,
    get_coverage_summary,
    get_file_language_map,
    get_git_metadata_bulk,
    get_node_degree_counts_bulk,
    get_refactoring_suggestions,
    list_health_snapshots,
    load_coverage_for_repo,
    sort_metrics_worst_first,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import HealthFileMetric, HealthFinding
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    filter_rows_by_attr,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta


def _serialize_finding(f: HealthFinding) -> dict[str, Any]:
    try:
        details = json.loads(f.details_json) if f.details_json else {}
    except Exception:
        details = {}
    return {
        "biomarker_type": f.biomarker_type,
        "severity": f.severity,
        "file_path": f.file_path,
        "function_name": f.function_name,
        "line_start": f.line_start,
        "line_end": f.line_end,
        "health_impact": round(f.health_impact, 3),
        "reason": f.reason,
        "details": details,
        "status": f.status,
        # Health pillar this finding homes under (defect / maintainability /
        # performance) for per-dimension filtering.
        "dimension": getattr(f, "dimension", None) or "defect",
    }


def _serialize_refactoring(r: Any) -> dict[str, Any]:
    """Serialize a ``RefactoringSuggestion`` ORM row into a structured plan.

    The ``*_json`` columns are decoded back into their open dicts so an agent
    reads the concrete plan (the split groups, the evidence, the blast radius)
    rather than a prose string.
    """

    def _load(raw: str | None) -> Any:
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    return {
        # The persisted row id — pass it to ``generate_refactoring_code`` to
        # turn this plan into actual code + a diff (opt-in).
        "id": getattr(r, "id", None),
        "refactoring_type": r.refactoring_type,
        "file_path": r.file_path,
        "target_symbol": r.target_symbol,
        "line_start": r.line_start,
        "line_end": r.line_end,
        "plan": _load(r.plan_json),
        "evidence": _load(r.evidence_json),
        "impact_delta": round(r.impact_delta, 3),
        "effort_bucket": r.effort_bucket,
        "blast_radius": _load(r.blast_radius_json),
        "confidence": r.confidence,
        "source_biomarker": r.source_biomarker,
    }


def _in_dimensions(row: Any, dimensions: set[str]) -> bool:
    """True when *row* belongs to one of *dimensions* (empty set -> everything).

    ``dimension`` is nullable and a NULL means ``defect``: the column was added
    without a backfill, so pre-existing rows stay NULL until the next index
    recomputes them. Reading it as anything else drops real defect findings.
    """
    if not dimensions:
        return True
    return (row.dimension or "defect") in dimensions


def _round_opt(v: Any) -> float | None:
    """Round a nullable per-dimension score, preserving ``None`` (not measured)."""
    return round(v, 2) if v is not None else None


def _leads_by_file(findings: list[Any]) -> dict[str, dict[str, Any]]:
    """Reduce each file's findings to its dominant cause + pre-clamp magnitude.

    ``primary_biomarker`` / ``primary_reason`` give a low file "the one reason"
    to lead with; ``total_deduction`` (summed ``health_impact``) distinguishes
    two files that both floor at 1.0. Additive — the score itself is untouched.
    """
    by_file: dict[str, list[Any]] = {}
    for f in findings:
        by_file.setdefault(f.file_path, []).append(f)
    leads: dict[str, dict[str, Any]] = {}
    for path, fs in by_file.items():
        primary = max(fs, key=lambda x: x.health_impact)
        leads[path] = {
            "primary_biomarker": primary.biomarker_type,
            "primary_reason": primary.reason,
            "total_deduction": round(sum(float(x.health_impact or 0.0) for x in fs), 3),
        }
    return leads


def _serialize_metric(m: HealthFileMetric, lead: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "file_path": m.file_path,
        "score": round(m.score, 2),
        "max_ccn": m.max_ccn,
        "max_nesting": m.max_nesting,
        "nloc": m.nloc,
        "has_test_file": m.has_test_file,
        "line_coverage_pct": m.line_coverage_pct,
        "branch_coverage_pct": m.branch_coverage_pct,
        "module": m.module,
        # Leverage: NLOC-weighted points this file drags below the Healthy band
        # (``(8.0 - score) * nloc``, 0 once healthy). This is how much the repo
        # headline recovers if the file reaches 8.0, so ranking by it — not by
        # raw score — points at the files that actually move the average. A tiny
        # 1.0 file and a 1200-line 1.0 file score the same but differ 40x here.
        "weighted_deficit": round(max(HEALTHY_MIN - m.score, 0.0) * max(m.nloc, 1)),
        # Per-dimension scores from the three-signal split. ``score`` is the
        # overall surfaced number (== ``defect_score`` for now);
        # ``performance_score`` is computed but not yet surfaced as its own pillar.
        "defect_score": _round_opt(getattr(m, "defect_score", None)),
        "maintainability_score": _round_opt(getattr(m, "maintainability_score", None)),
        "performance_score": _round_opt(getattr(m, "performance_score", None)),
        # Dominant-cause lead + pre-clamp magnitude (null when no findings for
        # this row). Lets a caller lead with the one reason and rank two floored
        # files by depth without re-reading every finding.
        "primary_biomarker": lead.get("primary_biomarker") if lead else None,
        "primary_reason": lead.get("primary_reason") if lead else None,
        "total_deduction": lead.get("total_deduction") if lead else None,
    }


def _serialize_coverage_row(row: Any) -> dict[str, Any]:
    try:
        covered = json.loads(row.covered_lines_json) if row.covered_lines_json else []
    except Exception:
        covered = []
    return {
        "file_path": row.file_path,
        "source_format": row.source_format,
        "line_coverage_pct": row.line_coverage_pct,
        "branch_coverage_pct": row.branch_coverage_pct,
        "covered_lines": covered,
        "total_coverable_lines": row.total_coverable_lines,
        "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
        "ingested_commit_sha": row.ingested_commit_sha,
    }


def _module_rollups(metrics: list[HealthFileMetric]) -> list[dict[str, Any]]:
    """NLOC-weighted module rollups derived from ``HealthFileMetric.module``.

    One row per module; ``None`` modules are dropped. Sorted by health
    ascending so the worst modules surface first — matches the per-file
    ordering and what the dashboard already expects.
    """
    buckets: dict[str, list[HealthFileMetric]] = {}
    for m in metrics:
        if m.module:
            buckets.setdefault(m.module, []).append(m)
    out: list[dict[str, Any]] = []
    for name, rows in buckets.items():
        total_nloc = sum(max(r.nloc, 1) for r in rows)
        if total_nloc:
            avg = sum(r.score * max(r.nloc, 1) for r in rows) / total_nloc
        else:
            avg = sum(r.score for r in rows) / len(rows)
        worst = min(rows, key=lambda r: r.score)
        out.append(
            {
                "module": name,
                "file_count": len(rows),
                "nloc": sum(r.nloc for r in rows),
                "average_health": round(avg, 2),
                "worst_performer_path": worst.file_path,
                "worst_performer_score": round(worst.score, 2),
            }
        )
    out.sort(key=lambda r: r["average_health"])
    return out


def _unresolved_targets(
    *,
    file_targets: list[str],
    module_targets: list[str],
    matched_modules: set[str],
    resolved_paths: set[str],
    excluded_paths: set[str],
    repo_root: Any,
) -> list[dict[str, str]]:
    """Name every requested target that produced no rows, with a reason.

    A dropped target is otherwise indistinguishable from a clean file: an
    empty ``findings`` list reads as "this file is healthy", which is the most
    damaging default this tool can have. The reason is the actionable part —
    ``not_indexed`` means run ``repowise update``, ``no_such_path`` means the
    target was a typo, ``excluded`` means the repo config drops it on purpose.
    """
    out: list[dict[str, str]] = []
    for t in file_targets:
        if t in resolved_paths:
            continue
        if t in excluded_paths:
            reason = "excluded"
        else:
            try:
                on_disk = (Path(repo_root) / t).exists()
            except (OSError, ValueError):
                on_disk = False
            reason = "not_indexed" if on_disk else "no_such_path"
        out.append({"target": t, "reason": reason})
    out.extend(
        {"target": f"module:{name}", "reason": "no_such_module"}
        for name in module_targets
        if name not in matched_modules
    )
    return out


def _directive(
    by_leverage: list[HealthFileMetric],
    leads: dict[str, dict[str, Any]],
    gap_points: int,
) -> dict[str, Any] | None:
    """The one file to fix first, and what fixing it buys.

    Every other block here ranks and describes; none of them recommends. That
    gap is why a correct finding can sit at position 12 of an undifferentiated
    list and change nobody's behaviour. Same role as ``get_risk``'s
    ``directive``: lead with the call, keep the evidence underneath.

    Ranked by ``weighted_deficit`` (not ``score``, which floors at 1.0), so
    this names the file that actually moves the repo average.
    """
    if not by_leverage:
        return None
    top = by_leverage[0]
    recovers = round(max(HEALTHY_MIN - top.score, 0.0) * max(top.nloc, 1))
    lead = leads.get(top.file_path) or {}
    return {
        "fix_first": top.file_path,
        "reason": lead.get("primary_reason") or f"scores {round(top.score, 2)}",
        # Points the repo headline recovers if this one file reaches Healthy,
        # and what share of the total gap that is — the "few files, not the
        # long tail" argument made concrete for a single file.
        "recovers_points": recovers,
        "share_of_repo_gap_pct": (round(100.0 * recovers / gap_points, 1) if gap_points else None),
        "then": [m.file_path for m in by_leverage[1:3]],
        "plan_via": "get_health(include=['refactoring'])",
    }


def _dimension_average(metrics: list[HealthFileMetric], attr: str) -> float | None:
    """NLOC-weighted headline over a per-dimension score attribute.

    Skips rows without the attribute (those predating that pillar) so the KPI
    reads "not measured" rather than a misleading 10.0; ``None`` when no row
    carries it.
    """
    scored = [m for m in metrics if getattr(m, attr, None) is not None]
    if not scored:
        return None
    total_nloc = sum(max(m.nloc, 1) for m in scored)
    if not total_nloc:
        return round(sum(getattr(m, attr) for m in scored) / len(scored), 2)
    return round(sum(getattr(m, attr) * max(m.nloc, 1) for m in scored) / total_nloc, 2)


def _gap_analysis(metrics: list[HealthFileMetric]) -> dict[str, Any]:
    """How few files must reach 8.0 for the *weighted average* to reach 8.0.

    Answers what the bare KPI cannot: the NLOC-weighted average is held down by a
    *few large low-scoring files*, not the long tail. The gap here is the **net**
    points the average needs (``8.0 * total_nloc - Σ score*nloc``) — healthy files
    already sit above 8.0 and cushion it, so this is smaller than the gross
    all-files-healthy deficit and is the number that matches the goal "move the
    average". ``files_to_reach_target`` is the punchline: lift the worst-deficit N
    files to 8.0 and the headline crosses 8.0. Pure over the metrics in hand.
    """
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    weighted_sum = sum(m.score * max(m.nloc, 1) for m in metrics)
    net_gap = HEALTHY_MIN * total_nloc - weighted_sum
    below = sorted(
        (
            max(HEALTHY_MIN - m.score, 0.0) * max(m.nloc, 1)
            for m in metrics
            if m.score < HEALTHY_MIN
        ),
        reverse=True,
    )
    if net_gap <= 0 or not below:
        return {
            "target_score": HEALTHY_MIN,
            "weighted_gap_points": 0,
            "files_below_target": len(below),
            "files_to_reach_target": 0,
            "files_for_half_gap": 0,
        }

    def _files_for(points: float) -> int:
        acc = 0.0
        for i, d in enumerate(below, 1):
            acc += d
            if acc >= points:
                return i
        return len(below)

    return {
        "target_score": HEALTHY_MIN,
        # Net weighted points the average must recover to reach 8.0.
        "weighted_gap_points": round(net_gap),
        "files_below_target": len(below),
        # The reframe: lift this many worst-deficit files to 8.0 and the weighted
        # average reaches 8.0; half that gap needs even fewer.
        "files_to_reach_target": _files_for(net_gap),
        "files_for_half_gap": _files_for(0.5 * net_gap),
    }


def _perf_kpis(performance_findings: int, coverage: PerfCoverage | None) -> dict[str, Any]:
    """The honest performance headline: finding count + density + coverage.

    Leads with *how many* findings and over *how much* of the code the perf pass
    ran, so an agent never reads a bare ``performance_average`` of ~10 as "fast"
    when the real story is "we could only analyze 3% of this repo".
    """
    density: float | None = None
    if coverage is not None and coverage.covered_nloc > 0:
        density = round(10000.0 * performance_findings / coverage.covered_nloc, 2)
    return {
        "performance_findings": performance_findings,
        "performance_findings_density_per_10k_loc": density,
        "performance_coverage_pct": (
            coverage.pct_loc if (coverage and coverage.analyzed_files) else None
        ),
        "performance_covered_files": coverage.covered_files if coverage else 0,
        "performance_analyzed_files": coverage.analyzed_files if coverage else 0,
        "performance_skipped_files": coverage.skipped_files if coverage else 0,
        "performance_unsupported_languages": (coverage.unsupported_languages if coverage else []),
    }


def _compute_kpis(
    metrics: list[HealthFileMetric],
    *,
    performance_findings: int = 0,
    coverage: PerfCoverage | None = None,
) -> dict[str, Any]:
    if not metrics:
        return {
            "file_count": 0,
            "average_health": 10.0,
            "worst_performer_path": None,
            "worst_performer_score": None,
            "maintainability_average": None,
            "performance_average": None,
            **_perf_kpis(0, None),
        }
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    avg = sum(m.score * max(m.nloc, 1) for m in metrics) / total_nloc
    worst = min(metrics, key=lambda r: r.score)
    return {
        "file_count": len(metrics),
        "average_health": round(avg, 2),
        # NLOC-weighted (``average_health``) vs plain file mean. When these
        # diverge, a few large low-scoring files are holding the headline down —
        # the weighted number is what the dashboard/badge surface, and the gap
        # between the two is the signal to chase big files, not the long tail.
        "average_health_weighting": "nloc",
        "average_health_unweighted": round(sum(m.score for m in metrics) / len(metrics), 2),
        "band": band_for(round(avg, 2)),
        "worst_performer_path": worst.file_path,
        "worst_performer_score": round(worst.score, 2),
        # Maintainability + performance pillar headlines alongside the
        # defect-backed average. Each is ``None`` until its pillar is measured.
        "maintainability_average": _dimension_average(metrics, "maintainability_score"),
        "performance_average": _dimension_average(metrics, "performance_score"),
        # Performance leads with count + density + coverage, not the diluted /10.
        **_perf_kpis(performance_findings, coverage),
    }


@mcp.tool()
async def get_health(
    targets: list[str] | None = None,
    include: list[str] | None = None,
    repo: str | None = None,
    limit: int = 20,
    only: list[str] | None = None,
) -> dict:
    """Code-health scores and findings — self-check a file before/after editing.

    No ``targets`` → repo dashboard, led by a ``directive`` naming the one file
    to fix first. With ``targets`` → per-file scores + findings. Rank by
    ``weighted_deficit``, not ``score``: the score floors at 1.0 and cannot
    separate the worst band.

    Three co-equal dimensions per file: ``score`` (defect risk, the headline),
    ``maintainability_score``, and ``performance_score`` (static I/O-in-loop /
    N+1 risk, never blended into the defect headline). Each finding carries its
    ``dimension``.

    Args:
        targets: file paths or ``module:<name>``. Empty → dashboard mode. Any
            target matching nothing is named in ``unresolved`` with a reason
            (``not_indexed`` → run ``repowise update`` | ``no_such_path`` |
            ``excluded`` | ``no_such_module``), so an empty ``findings`` means
            healthy and nothing else.
        include: opt-in blocks: ``biomarkers`` | ``refactoring`` | ``trend`` |
            ``coverage`` | ``accuracy`` | ``signals`` | ``churn_complexity`` |
            ``performance``/``defect``/``maintainability`` (filter findings to
            one dimension).
        only: keep just these top-level keys. ``include`` adds blocks, ``only``
            subtracts them — pass ``["directive"]`` for the cheapest useful call.
        repo: usually omitted.
        limit: max rows in every ranked list (capped at 50); each carries a
            ``*_total`` sibling so truncation is never silent.
    """
    started = perf_counter()
    limit = min(max(limit, 1), 50)
    include_set = set(include or [])
    only_set = set(only or [])

    def wants(block: str) -> bool:
        """True when ``block`` survives the ``only`` projection.

        ``only`` used to be applied to the finished response, so the cheapest
        documented call — ``only=["directive"]`` — still paid for every block it
        then discarded. Consulted before the expensive optional work so the
        projection gates the work as well as the payload.
        """
        return not only_set or block in only_set

    # Resolved before the reads, not after them. Applied to the finished
    # response, a dimension filter narrowed a list that had already been capped
    # by impact — and performance findings carry low impact by construction, so
    # ``include=["biomarkers", "performance"]`` filtered a defect-heavy head down
    # to nothing while the total still reported the whole repo. The filter now
    # decides which rows are eligible for the cap in the first place.
    dimension_filter = include_set & {"performance", "defect", "maintainability"}
    # The serialized-rows read is the expensive optional one; skip it when no
    # block that carries findings survives the projection.
    wants_findings = wants("findings") or wants("top_findings")

    # Split ``module:foo`` targets out of the path list. A target that
    # matches one or more modules is expanded into the set of files
    # belonging to those modules.
    raw_targets = list(targets or [])
    module_targets = [t.split(":", 1)[1] for t in raw_targets if t.startswith("module:")]
    # Stored paths are POSIX-separated. Normalize so a Windows caller passing
    # ``packages\core\x.py`` matches instead of coming back ``no_such_path``.
    file_targets = [t.replace("\\", "/") for t in raw_targets if not t.startswith("module:")]

    ctx = await _resolve_repo_context(repo)
    # Performance headline inputs (dashboard mode): filled inside the session.
    perf_coverage: PerfCoverage | None = None
    perf_findings_count = 0
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)

        all_metrics_q = select(HealthFileMetric).where(
            HealthFileMetric.repository_id == repository.id
        )
        exclude_spec = _get_exclude_spec(ctx.path)
        indexed_rows = list((await session.execute(all_metrics_q)).scalars().all())
        all_metrics = filter_rows_by_attr(indexed_rows, "file_path", exclude_spec)
        # Paths the index knows about but the exclude config drops. Kept so an
        # unresolved target can report "excluded" (a config decision) rather
        # than "no_such_path" (a typo) — the two need different responses.
        excluded_paths = {m.file_path for m in indexed_rows} - {m.file_path for m in all_metrics}

        matched_modules: set[str] = set()
        if module_targets:
            module_set = set(module_targets)
            for m in all_metrics:
                if m.module in module_set:
                    matched_modules.add(m.module)
                    file_targets.append(m.file_path)
            file_targets = sorted(set(file_targets))

        # A non-empty ``targets`` means the caller asked for a scope, and that
        # holds even when nothing resolves. Keying the mode off the *resolved*
        # paths let ``targets=["module:typo"]`` fall through to dashboard mode
        # and answer a module-scoped question with repo-wide numbers — an
        # answer that reads as scoped and is not.
        scoped = bool(raw_targets)
        effective_targets = file_targets if scoped else []
        nothing_resolved = scoped and not effective_targets

        open_findings = (
            HealthFinding.repository_id == repository.id,
            HealthFinding.status == "open",
        )
        # Two row sets, deliberately split.
        #
        # ``finding_rows`` are the ones this response will serialize.
        # ``lead_rows`` is the wider set the per-file dominant-cause reduction
        # and the exact totals are computed from — it only ever needs four
        # columns.
        #
        # Targeted mode asks about a handful of files, so one full read serves
        # both. Dashboard mode does not: hydrating every open finding as a full
        # ORM object to emit ``limit`` of them measured 262ms on this repo, and
        # that cost is linear in finding count, so it grows with the repo the
        # dashboard is describing.
        if scoped:
            finding_rows = filter_rows_by_attr(
                list(
                    (
                        await session.execute(
                            select(HealthFinding)
                            .where(*open_findings)
                            .where(HealthFinding.file_path.in_(effective_targets))
                            .order_by(HealthFinding.health_impact.desc())
                        )
                    )
                    .scalars()
                    .all()
                ),
                "file_path",
                exclude_spec,
            )
            lead_rows: list[Any] = finding_rows
            emitted = [f for f in finding_rows if _in_dimensions(f, dimension_filter)]
            finding_rows = emitted[:limit]
        else:
            # Narrow read over every open finding: the four attributes
            # ``_leads_by_file`` reads, plus ``dimension`` for the perf headline
            # and ``id`` to fetch the head. SQLAlchemy ``Row`` exposes these as
            # attributes, so the reduction and the exclude filter both run
            # against it unchanged.
            lite_rows = list(
                (
                    await session.execute(
                        select(
                            HealthFinding.id,
                            HealthFinding.file_path,
                            HealthFinding.health_impact,
                            HealthFinding.biomarker_type,
                            HealthFinding.reason,
                            HealthFinding.dimension,
                        )
                        .where(*open_findings)
                        .order_by(HealthFinding.health_impact.desc())
                    )
                ).all()
            )
            # ``lead_rows`` stays the unfiltered open set: it feeds the per-file
            # leads and the performance KPI, neither of which should change
            # because the caller asked to *see* one dimension.
            lead_rows = filter_rows_by_attr(lite_rows, "file_path", exclude_spec)
            emitted = [r for r in lead_rows if _in_dimensions(r, dimension_filter)]
            # Fetch the head by id rather than re-running the ranked query with
            # an over-fetch margin. The margin had to cover every exclusion in
            # the table, so a repo excluding a large subtree turned the "capped"
            # read back into a near-full one; by id it is exactly ``limit`` rows
            # whatever the exclude config or dimension filter say.
            head_ids = [r.id for r in emitted[:limit]]
            finding_rows = []
            if head_ids and wants_findings:
                by_id = {
                    f.id: f
                    for f in (
                        await session.execute(
                            select(HealthFinding).where(HealthFinding.id.in_(head_ids))
                        )
                    )
                    .scalars()
                    .all()
                }
                # Re-imposed from ``head_ids``; ``IN`` does not preserve order.
                finding_rows = [by_id[i] for i in head_ids if i in by_id]

        # Counts the rows this response is about: the post-exclusion open set,
        # narrowed to the requested dimensions when one was asked for. Reporting
        # the unfiltered total beside a filtered list is what made an empty
        # ``findings`` read as "nothing here" rather than "nothing shown".
        findings_total = len(emitted)

        # Worst-first order, placed here because ranking needs the summed
        # deduction per file and ``lead_rows`` is the first point that carries
        # every open finding this response is entitled to see. Same comparator
        # the crud layer applies to ``get_health_metrics``, so the REST
        # dashboard and this tool cannot disagree about which file is worst —
        # but fed from rows already in memory, so it costs no extra query.
        #
        # Deliberately ``lead_rows`` (the unfiltered open set) rather than
        # ``emitted``: asking to *see* one dimension must not restate which
        # files the repo's worst are.
        deduction_by_path: dict[str, float] = {}
        for f in lead_rows:
            deduction_by_path[f.file_path] = deduction_by_path.get(f.file_path, 0.0) + float(
                f.health_impact or 0.0
            )
        # Rebound rather than kept beside a sorted copy, and above every reader.
        # ``kpis``, the module rollup, the leverage view and the churn quadrant
        # all reduce with ``min()`` or a stable sort, which resolve ties by
        # *input* order — so leaving them on the raw list would have one
        # response name one file as the worst performer while the
        # ``worst_files`` list printed below it led with another.
        all_metrics = sort_metrics_worst_first(all_metrics, deduction_by_path)
        metric_rows = (
            [m for m in all_metrics if m.file_path in set(effective_targets)]
            if scoped
            else all_metrics
        )

        # Dashboard perf headline: coverage (how much of the analyzed code the
        # perf pass ran on) + open performance-finding count. Both feed ``kpis``
        # alone, so a projection that drops kpis skips the language-map read.
        if not scoped and wants("kpis"):
            lang_by_path = await get_file_language_map(session, repository.id)
            perf_coverage = coverage_for_metrics(all_metrics, lang_by_path)
            perf_findings_count = sum(
                1 for f in lead_rows if (f.dimension or "defect") == "performance"
            )

        # ``accuracy`` scores the ranking against the whole repo rather than the
        # capped head, but it reads exactly one biomarker: ``compute_defect_accuracy``
        # ignores every finding whose type is not ``prior_defect``. Selecting
        # those directly keeps the honest denominator without re-reading the
        # ~10k rows the narrow pass above exists to avoid.
        accuracy_rows: list[Any] = []
        if "accuracy" in include_set and not scoped:
            accuracy_rows = filter_rows_by_attr(
                list(
                    (
                        await session.execute(
                            select(HealthFinding)
                            .where(*open_findings)
                            .where(HealthFinding.biomarker_type == "prior_defect")
                        )
                    )
                    .scalars()
                    .all()
                ),
                "file_path",
                exclude_spec,
            )

        # Structured refactoring plans (Extract Class, ...) — loaded only when
        # asked for, scoped to the same targets, exclude-filtered like findings.
        refactoring_rows: list[Any] = []
        if "refactoring" in include_set and not nothing_resolved:
            refactoring_rows = filter_rows_by_attr(
                await get_refactoring_suggestions(
                    session,
                    repository.id,
                    file_paths=list(effective_targets) if scoped else None,
                ),
                "file_path",
                exclude_spec,
            )

        coverage_rows: list[Any] = []
        coverage_summary: dict[str, Any] = {}
        if "coverage" in include_set and not nothing_resolved:
            coverage_rows = filter_rows_by_attr(
                # ``effective_targets``, not ``targets`` — a raw ``module:foo``
                # target is not a file path and matched nothing here.
                await load_coverage_for_repo(
                    session, repository.id, file_paths=list(effective_targets) if scoped else None
                ),
                "file_path",
                exclude_spec,
            )
            # coverage_summary is a repo-wide stored aggregate, not recomputed
            # here; the per-file rows above are exclude-filtered.
            coverage_summary = await get_coverage_summary(session, repository.id)

        # Per-file process/people/topology signals for targeted files — the
        # same join the file-detail drawer and REST breakdown use, so an agent
        # can read why a file is risky (prior defects, churn, owners, degree)
        # before touching it. Targeted mode only; the target set is small.
        signals_by_path: dict[str, dict[str, Any]] = {}
        if "signals" in include_set and effective_targets:
            # Batched, not per-file. This loop used to issue three round-trips
            # per target (git metadata, graph node, degree counts) — the exact
            # cross-function N+1 the tool's own ``io_in_loop`` biomarker flags
            # here. ``module:`` targets expand to every file in the module, so
            # the target set is not always small.
            git_meta_by_path = await get_git_metadata_bulk(
                session, repository.id, list(effective_targets)
            )
            degrees_by_path = await get_node_degree_counts_bulk(
                session, repository.id, list(effective_targets)
            )
            for path in effective_targets:
                signals_by_path[path] = asdict(
                    file_signals(git_meta_by_path.get(path), degrees_by_path.get(path))
                )

        # Churn x complexity quadrant for the whole repo (dashboard mode). One
        # git-metadata query joined against the already-loaded metrics.
        churn_points: list[dict[str, Any]] = []
        if "churn_complexity" in include_set and not scoped:
            git_meta_by_path = await get_all_git_metadata(session, repository.id)
            churn_points = [
                asdict(p) for p in churn_complexity_points(all_metrics, git_meta_by_path)[:limit]
            ]

        # Load the snapshot window for the repo-level trend block and/or the
        # per-file trajectory we attach in targeted mode ("should I touch this
        # file" context for agents).
        snapshots: list[Any] = []
        if "trend" in include_set or scoped:
            snapshots = await list_health_snapshots(session, repository.id, limit=20)

    kpis = _compute_kpis(
        metric_rows if scoped else all_metrics,
        performance_findings=perf_findings_count,
        coverage=perf_coverage,
    )
    # Dominant-cause lead per file. Targeted mode wants one per target, so the
    # reduction runs over the whole (small) scoped set. Dashboard mode only ever
    # prints a lead for the files it emits, so it reduces just those rows
    # instead of all ~10k — identical output, and ``_leads_by_file`` measured
    # ~148ms per call when handed the full set.
    if scoped:
        by_leverage: list[HealthFileMetric] = []
        lead_source: list[Any] = lead_rows
    else:
        # Leverage view: files ranked by NLOC-weighted deficit (how much each
        # drags the headline), not by raw score. Distinct from worst_files — a
        # big warning-band file outranks a tiny alert-band one here because
        # fixing it moves the average far more. Computed before the leads so the
        # set of printed files is known.
        by_leverage = sorted(
            (m for m in all_metrics if m.score < HEALTHY_MIN),
            key=lambda m: max(HEALTHY_MIN - m.score, 0.0) * max(m.nloc, 1),
            reverse=True,
        )
        printed = {m.file_path for m in metric_rows[:limit]}
        printed |= {m.file_path for m in by_leverage[:limit]}
        lead_source = [r for r in lead_rows if r.file_path in printed]
    leads = _leads_by_file(lead_source)

    if scoped:
        metric_payload: list[dict[str, Any]] = []
        for m in metric_rows:
            row = _serialize_metric(m, leads.get(m.file_path))
            if m.file_path in signals_by_path:
                row["signals"] = signals_by_path[m.file_path]
            metric_payload.append(row)
        result: dict[str, Any] = {
            "mode": "targets",
            "targets": raw_targets,
            "metrics": metric_payload,
            # Capped like every other ranked list, with the total alongside so
            # the truncation is visible rather than inferred from the length.
            "findings": [_serialize_finding(f) for f in finding_rows[:limit]],
            "findings_total": findings_total,
        }
        unresolved = _unresolved_targets(
            file_targets=file_targets,
            module_targets=module_targets,
            matched_modules=matched_modules,
            resolved_paths={m.file_path for m in metric_rows},
            excluded_paths=excluded_paths,
            repo_root=ctx.path,
        )
        if unresolved:
            result["unresolved"] = unresolved
            if any(u["reason"] == "no_such_module" for u in unresolved):
                # ``module:`` has no discovery call of its own, so a bad name
                # would otherwise cost a full dashboard round-trip to correct.
                result["known_modules"] = sorted({m.module for m in all_metrics if m.module})
        # Per-file score trajectory for each target — silent (omitted) when a
        # file has < 2 snapshots of history rather than a misleading flat line.
        trends = []
        for m in metric_rows:
            t = file_trend(snapshots, m.file_path)
            if not t.points:
                continue
            trends.append(
                {
                    "file_path": t.file_path,
                    "series": [round(p.score, 2) for p in t.points],
                    "current": t.current,
                    "delta": t.delta,
                    "declining": t.declining,
                }
            )
        if trends:
            result["trends"] = trends
        if module_targets:
            in_modules = [m for m in all_metrics if m.module in set(module_targets)]
            result["modules"] = _module_rollups(in_modules)
    else:
        # Dashboard mode — top-N worst files + headline findings + the
        # per-module rollup so the overview page doesn't need a second
        # round-trip. ``by_leverage`` is built above, before the leads.
        # Same serializer as worst_files, so every row carries
        # weighted_deficit for the caller to sort on further.
        all_modules = _module_rollups(all_metrics)
        gap = _gap_analysis(all_metrics)
        result = {
            "mode": "dashboard",
            # Lead with the call, not the data. Every block below ranks and
            # describes; this one recommends.
            "directive": _directive(by_leverage, leads, gap.get("weighted_gap_points") or 0),
            "kpis": kpis,
            "distribution": health_distribution(all_metrics),
            # Where the gap to Healthy concentrates — the "few files, not the
            # long tail" reframe that turns a repo-wide number into a short list.
            "gap_analysis": gap,
            "worst_files": [
                _serialize_metric(m, leads.get(m.file_path)) for m in metric_rows[:limit]
            ],
            "high_leverage_files": [
                _serialize_metric(m, leads.get(m.file_path)) for m in by_leverage[:limit]
            ],
            "top_findings": [_serialize_finding(f) for f in finding_rows[:limit]],
            "top_findings_total": findings_total,
            # Worst-first, so the cap keeps the modules worth looking at. On a
            # monorepo the tail is dozens of single-file buckets.
            "modules": all_modules[:limit],
            "modules_total": len(all_modules),
        }
        if "churn_complexity" in include_set:
            result["churn_complexity"] = churn_points
        if "accuracy" in include_set:
            # Self-validation: does the score rank the buggy files first?
            # Scored over the full open set (``accuracy_rows``), not the capped
            # head — ranking quality measured on the top 20 would be circular.
            # ``None`` when there isn't enough signal for an honest number.
            result["defect_accuracy"] = compute_defect_accuracy(
                all_metrics,
                [_serialize_finding(f) for f in accuracy_rows],
            )

    if "biomarkers" in include_set and "findings" not in result:
        # Capped like every other ranked list. Uncapped, this was the one block
        # in the tool that could return the repo's entire open finding set: on a
        # 3.2k-file repo ``include=["biomarkers"]`` with no targets served 10.3k
        # rows / 4.7MB, which overflows an agent's context and returns nothing
        # usable. Findings arrive impact-ordered, so the cap keeps the ones
        # worth reading.
        result["findings"] = [_serialize_finding(f) for f in finding_rows[:limit]]
        result["findings_total"] = findings_total

    if "refactoring" in include_set:
        # Structured refactoring plans (the concrete split groups / evidence /
        # blast radius) for detectors that have one — the upgrade over the old
        # prose-string suggestions.
        #
        # Rank by the *file's* weighted deficit first, per-plan impact_delta
        # second, and cap to ``limit``. Two reasons: an un-capped dump is a
        # thousands-of-plans firehose that blows the token budget, and a pure
        # impact_delta sort buries the highest-leverage plans — a Split File on a
        # 1200-line hotspot recovers the most repo-average yet can carry a modest
        # per-file delta, so file leverage has to lead. ``refactoring_plans_total``
        # keeps the truncation honest.
        deficit_by_path = {
            m.file_path: round(max(HEALTHY_MIN - m.score, 0.0) * max(m.nloc, 1))
            for m in all_metrics
        }
        ranked = sorted(
            refactoring_rows,
            key=lambda r: (deficit_by_path.get(r.file_path, 0), r.impact_delta or 0.0),
            reverse=True,
        )
        result["refactoring_plans"] = [
            {
                **_serialize_refactoring(r),
                "file_weighted_deficit": deficit_by_path.get(r.file_path, 0),
            }
            for r in ranked[:limit]
        ]
        result["refactoring_plans_total"] = len(refactoring_rows)
        # The deterministic prose suggestion is the fallback for biomarkers
        # that have no structured detector yet. It is emitted once per
        # biomarker type as ``suggestion_legend`` (built below, after the
        # dimension filter) rather than copied onto every finding: the text is
        # keyed purely by type, so the per-row form repeated one ~40-word
        # string up to 10x in a single response.
        #
        # (The old no-findings-anywhere fallback here was unreachable: targeted
        # mode always sets ``findings`` and dashboard mode always sets
        # ``top_findings``.)

    if "trend" in include_set:
        summary = diff_snapshots(snapshots)
        result["trend"] = {
            "current_hotspot_health": summary.current_hotspot_health,
            "current_average_health": summary.current_average_health,
            "previous_hotspot_health": summary.previous_hotspot_health,
            "previous_average_health": summary.previous_average_health,
            "hotspot_delta": summary.hotspot_delta,
            "average_delta": summary.average_delta,
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
            "recent": recent_kpis(snapshots, limit=10),
        }

    if "coverage" in include_set:
        # Drop the bulky covered-lines arrays from dashboard mode; full
        # detail is available in targeted mode.
        if targets:
            coverage_payload = [_serialize_coverage_row(r) for r in coverage_rows]
        else:
            coverage_payload = [
                {k: v for k, v in _serialize_coverage_row(r).items() if k != "covered_lines"}
                for r in coverage_rows[:limit]
            ]
        # ``ingested_at`` is a datetime on the summary too — coerce.
        if coverage_summary.get("ingested_at") is not None:
            coverage_summary = {
                **coverage_summary,
                "ingested_at": coverage_summary["ingested_at"].isoformat(),
            }
        result["coverage"] = {
            "summary": coverage_summary,
            "files": coverage_payload,
        }

    # (The dimension filter — ``include=["performance"]`` and friends, so an
    # agent can ask "show me only the performance risk in this change" — is
    # applied where the rows are selected, not here. Filtering the finished
    # response meant filtering a list already capped by impact.)

    # One entry per biomarker type actually present in the findings this
    # response carries. Built last so the dimension filter above has already
    # narrowed the rows the caller will join against.
    if "refactoring" in include_set:
        present_types = {
            row.get("biomarker_type")
            for field in ("findings", "top_findings")
            for row in result.get(field) or ()
        }
        result["suggestion_legend"] = {
            bt: suggestion_for(bt) for bt in sorted(t for t in present_types if t)
        }

    # Projection. ``include`` could only ever add blocks, so asking for one
    # extra block re-shipped the whole dashboard with it; ``only`` is the
    # subtract half. Applied last so it can drop anything above, and ``mode`` /
    # ``_meta`` always survive — a response the caller cannot orient in is not
    # a saving.
    if only:
        keep = set(only) | {"mode"}
        # A key that does not exist in this response is named rather than
        # quietly yielding an empty one — same rule as ``unresolved`` above.
        # A misspelled projection is otherwise indistinguishable from a block
        # the repo genuinely has no data for.
        unknown = sorted(k for k in only if k not in result)
        result = {k: v for k, v in result.items() if k in keep}
        if unknown:
            result["unknown_only_keys"] = unknown

    # Targeted mode scopes the stale signal to the asked-about files; the
    # dashboard (no targets) keeps the repo-level warning.
    result["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
    # When the health pass last ran, which is a separate pass from indexing and
    # can lag it. ``_build_meta``'s fields all describe the *index*, so a stale
    # health row was previously invisible.
    analyzed = [m.updated_at for m in all_metrics if getattr(m, "updated_at", None)]
    if analyzed:
        result["_meta"]["health_analyzed_at"] = max(analyzed).isoformat()
    # Server-side wall clock, as ``get_context`` already reports. Without it a
    # regression in here is invisible until someone profiles it by hand.
    result["_meta"]["timing_ms"] = round((perf_counter() - started) * 1000, 2)
    return result
