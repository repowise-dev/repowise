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
    get_test_file_paths,
    list_health_snapshots,
    load_coverage_for_repo,
    sort_metrics_worst_first,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    HealthFileMetric,
    HealthFinding,
    RefactoringSuggestion,
)
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


# ``include`` and ``only`` were different vocabularies: the block a caller
# switches on with ``include=["biomarkers"]`` lands under the key ``findings``,
# so the obvious ``only=["biomarkers"]`` projected it away again. Alias the three
# that have a 1:1 key rather than make the caller learn two names for one block.
# ``signals`` is deliberately absent — it has no top-level key to alias to, it
# merges into ``metrics[].signals``, so it stays reported in ``unknown_only_keys``.
_ONLY_ALIASES = {
    "biomarkers": "findings",
    "accuracy": "defect_accuracy",
    "refactoring": "refactoring_plans",
}

# Files the directive reduces over: ``fix_first`` plus the two in ``then``. Named
# because three separate places have to agree on it — the plan lookup, the lead
# set the directive reads, and the directive itself — and they are far apart.
_DIRECTIVE_CANDIDATES = 3


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


def _serialize_coverage_row(row: Any, *, covered_lines: bool = True) -> dict[str, Any]:
    """One coverage row. ``covered_lines=False`` omits the per-line array.

    The narrow form is not the wide form minus a key: a row read with
    ``include_covered_lines=False`` carries no ``covered_lines_json`` at all, so
    touching it would raise rather than merely waste the parse.
    """
    out: dict[str, Any] = {
        "file_path": row.file_path,
        "source_format": row.source_format,
        "line_coverage_pct": row.line_coverage_pct,
        "branch_coverage_pct": row.branch_coverage_pct,
    }
    # Inserted here rather than appended, so the wide form stays byte-identical
    # to what callers already receive.
    if covered_lines:
        try:
            out["covered_lines"] = (
                json.loads(row.covered_lines_json) if row.covered_lines_json else []
            )
        except Exception:
            out["covered_lines"] = []
    out["total_coverable_lines"] = row.total_coverable_lines
    out["ingested_at"] = row.ingested_at.isoformat() if row.ingested_at else None
    out["ingested_commit_sha"] = row.ingested_commit_sha
    return out


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
    plan_biomarkers_by_path: dict[str, set[str]] | None = None,
    plan_count_by_path: dict[str, int] | None = None,
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
    # Does anything behind ``plan_via`` actually address the cause named in
    # ``reason``? Plans carry the biomarker that produced them, and several
    # biomarkers have no detector at all — ``coverage_gradient`` above all, which
    # no plan kind can answer because none of them writes tests. Saying so beats
    # routing the caller to plans for a different problem with full confidence.
    lead_biomarker = lead.get("primary_biomarker")
    available = (plan_biomarkers_by_path or {}).get(top.file_path, set())
    addresses = bool(lead_biomarker) and lead_biomarker in available
    out = {
        "fix_first": top.file_path,
        "reason": lead.get("primary_reason") or f"scores {round(top.score, 2)}",
        # Points the repo headline recovers if this one file reaches Healthy,
        # and what share of the total gap that is — the "few files, not the
        # long tail" argument made concrete for a single file.
        "recovers_points": recovers,
        "share_of_repo_gap_pct": (round(100.0 * recovers / gap_points, 1) if gap_points else None),
        "then": [m.file_path for m in by_leverage[1:3]],
        "plan_via": "get_health(include=['refactoring'])",
        "plan_addresses_reason": addresses,
    }
    # Only speak when there is a named cause to speak about. With no lead the
    # ``reason`` above already falls back to the bare score, and a note reading
    # "No stored plan addresses None" would be worse than silence.
    if not addresses and lead_biomarker:
        # Name the gap rather than leaving the caller to diff two biomarker
        # vocabularies. The three branches call for different next moves:
        # plans for other causes, plans with no recorded cause, or no plans.
        n_plans = (plan_count_by_path or {}).get(top.file_path, 0)
        if available:
            # Deliberately "target X, Y" rather than "the plans target only X, Y":
            # plans with an empty ``source_biomarker`` are counted in ``n_plans``
            # but cannot be named, so an exhaustive phrasing would be a claim
            # this read cannot support.
            out["plan_note"] = (
                f"No stored plan addresses {lead_biomarker!r}; the plans on this file "
                f"target {', '.join(sorted(available))}. Treat plan_via as related "
                f"cleanup, not the fix for reason."
            )
        elif n_plans:
            out["plan_note"] = (
                f"No stored plan addresses {lead_biomarker!r}; this file's {n_plans} "
                f"plan(s) record no source biomarker. Treat plan_via as related "
                f"cleanup, not the fix for reason."
            )
        else:
            out["plan_note"] = (
                f"No stored plan addresses {lead_biomarker!r}, and this file has no "
                f"plans at all. plan_via will return plans for other files."
            )
    return out


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

    Three dimensions per file: ``score`` (defect risk, the headline),
    ``maintainability_score``, ``performance_score`` (static I/O-in-loop / N+1
    risk, never blended in). Each finding carries its ``dimension``. Dashboard
    mode buckets test material: ``top_findings`` is production,
    ``test_findings`` the rest, each row says ``is_test``.

    Args:
        targets: file paths or ``module:<name>``. Empty → dashboard mode. A
            target matching nothing is named in ``unresolved`` with a reason
            (``not_indexed`` → run ``repowise update`` | ``no_such_path`` |
            ``excluded`` | ``no_such_module``), so empty ``findings`` means
            healthy.
        include: ``biomarkers`` | ``refactoring`` | ``trend`` | ``coverage`` |
            ``accuracy`` | ``signals`` | ``churn_complexity`` |
            ``performance``/``defect``/``maintainability`` (dimension).
        only: keep just these top-level keys — ``["directive"]`` is the cheapest
            useful call. Each kept list's ``*_total`` is retained too, and the
            ``include`` names work as aliases. Unmatched keys land in
            ``unknown_only_keys``.
        repo: usually omitted.
        limit: max rows per ranked list (max 50, ``0`` for none); each carries
            a ``*_total`` sibling so truncation is never silent.
    """
    started = perf_counter()
    # ``0`` means none, matching the ``module_limit`` convention on the REST
    # coverage route. It used to clamp up to 1, so the documented way to ask for
    # "the totals, none of the rows" silently returned a row.
    limit = min(max(limit, 0), 50)
    include_set = set(include or [])
    only_list = [_ONLY_ALIASES.get(k, k) for k in (only or [])]
    only_set = set(only_list)

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
    wants_test_findings = wants("test_findings")
    # Everything downstream of the test/production split, in one place.
    #
    # Keep this list exhaustive. The read it gates is not free (the column list
    # is narrow but the predicate is not indexed, so it scans this repo's graph
    # nodes — ~55 ms warm on a 35k-node index), and ``only=["directive"]`` /
    # ``["kpis"]`` / ``["modules"]`` serialize no metric row and no finding.
    # But a *missing* entry here is worse than the read: it makes the split
    # collapse for that projection, which is the same "a projection changed
    # what a surviving key holds" defect this change exists to close. Adding
    # ``suggestion_legend`` was not optional — the legend derives from the split
    # heads, and leaving it out silently reverted that fix.
    needs_test_paths = (
        wants_findings
        or wants_test_findings
        or wants("worst_files")
        or wants("high_leverage_files")
        or wants("metrics")
        or ("refactoring" in include_set and wants("suggestion_legend"))
    )

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
        # Test material, from the flag ingestion already decided per file.
        # Gated on ``needs_test_paths`` — see the note at its definition.
        test_paths: set[str] = set()
        if needs_test_paths:
            test_paths = await get_test_file_paths(session, repository.id)
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
        #
        # ``test_finding_rows`` is the dashboard-only test bucket (see the split
        # below). Targeted mode never fills it: the caller named the files, so
        # partitioning what they explicitly asked about would be answering a
        # different question than the one they asked.
        test_finding_rows: list[Any] = []
        test_findings_total = 0
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
            legend_rows: list[Any] = finding_rows
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
            # Test material goes in its own bucket rather than competing for
            # the repo's headline finding list. Measured on this repo, **2 of
            # the top 5** open findings by impact sit on test files, and 4-5 of
            # the top 20 — the top-20 figure is tie-dependent (ranks 14+ are all
            # at impact 2.16), which is itself the point: a fifth of the
            # most-read list was the test suite, decided partly by tie-break.
            # Splitting keeps both readable. A thrashing test suite is a real
            # signal some teams want; it is just not the same question as
            # "where is the defect risk in this codebase".
            #
            # Split *before* the cap, so each list is the top ``limit`` of its
            # own population — capping first and partitioning after would give
            # the smaller bucket whatever happened to land in the head.
            prod_emitted = [r for r in emitted if r.file_path not in test_paths]
            test_emitted = [r for r in emitted if r.file_path in test_paths]
            # Both heads, in one list, decided here rather than downstream: the
            # legend has to be a pure function of the ranked set so no
            # projection can change what a surviving key contains.
            legend_rows: list[Any] = prod_emitted[:limit] + test_emitted[:limit]
            # Fetch the head by id rather than re-running the ranked query with
            # an over-fetch margin. The margin had to cover every exclusion in
            # the table, so a repo excluding a large subtree turned the "capped"
            # read back into a near-full one; by id it is exactly ``limit`` rows
            # whatever the exclude config or dimension filter say.
            head_ids = [r.id for r in prod_emitted[:limit]]
            test_head_ids = [r.id for r in test_emitted[:limit]] if wants_test_findings else []
            finding_rows = []
            if not wants_findings:
                head_ids = []
            # One read for both heads — the split is a partition of the same
            # ranked set, so paying two round-trips for it would be the N+1 this
            # tool flags in itself.
            wanted_ids = head_ids + test_head_ids
            if wanted_ids:
                by_id = {
                    f.id: f
                    for f in (
                        await session.execute(
                            select(HealthFinding).where(HealthFinding.id.in_(wanted_ids))
                        )
                    )
                    .scalars()
                    .all()
                }
                # Re-imposed from the id lists; ``IN`` does not preserve order.
                finding_rows = [by_id[i] for i in head_ids if i in by_id]
                test_finding_rows = [by_id[i] for i in test_head_ids if i in by_id]
            test_findings_total = len(test_emitted)

        # Counts the rows this response is about: the post-exclusion open set,
        # narrowed to the requested dimensions when one was asked for. Reporting
        # the unfiltered total beside a filtered list is what made an empty
        # ``findings`` read as "nothing here" rather than "nothing shown".
        #
        # In dashboard mode this counts the *production* half, because that is
        # the list it sits beside; ``test_findings_total`` counts the other half
        # and the two still sum to the whole open set. Same rule #1337 settled
        # for the dimension filter: a total describes the list it is a sibling
        # of, never a wider set the caller cannot see.
        findings_total = len(emitted if scoped else prod_emitted)

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
                #
                # Only targeted mode serializes ``covered_lines``. The dashboard
                # used to read every ``covered_lines_json`` blob, ``json.loads``
                # each one, and then strip the field back out with a dict
                # comprehension — 466,874 B of parse per call for a key it never
                # emitted. Decline the column at the read instead.
                await load_coverage_for_repo(
                    session,
                    repository.id,
                    file_paths=list(effective_targets) if scoped else None,
                    include_covered_lines=scoped,
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

        # Dominant-cause lead per file. Targeted mode wants one per target, so
        # the reduction runs over the whole (small) scoped set. Dashboard mode
        # only ever prints a lead for the files it emits, so it reduces just
        # those rows instead of all ~10k — identical output, and
        # ``_leads_by_file`` measured ~148ms per call handed the full set.
        #
        # Computed inside the session because the directive's plan lookup below
        # needs ``by_leverage`` and has to run before the session closes.
        if scoped:
            by_leverage: list[HealthFileMetric] = []
            lead_source: list[Any] = lead_rows
        else:
            # Leverage view: files ranked by NLOC-weighted deficit (how much
            # each drags the headline), not by raw score. Distinct from
            # worst_files — a big warning-band file outranks a tiny alert-band
            # one here because fixing it moves the average far more. Computed
            # before the leads so the set of printed files is known.
            by_leverage = sorted(
                (m for m in all_metrics if m.score < HEALTHY_MIN),
                key=lambda m: max(HEALTHY_MIN - m.score, 0.0) * max(m.nloc, 1),
                reverse=True,
            )
            printed = {m.file_path for m in metric_rows[:limit]}
            printed |= {m.file_path for m in by_leverage[:limit]}
            # The directive's three candidates, unconditionally — it reads
            # ``by_leverage[:3]`` and is not a ranked list, so its leads must not
            # depend on ``limit``. Before ``limit=0`` existed this was covered by
            # the clamp to 1 only by accident; at 0 the lead set came back empty
            # and the directive degraded to a fallback ``reason`` ("scores 1.0")
            # *and* asserted ``plan_addresses_reason: false`` on every file —
            # a wrong claim rather than a missing one.
            printed |= {m.file_path for m in by_leverage[:_DIRECTIVE_CANDIDATES]}
            lead_source = [r for r in lead_rows if r.file_path in printed]
        leads = _leads_by_file(lead_source)

        # Which biomarkers the stored plans for the directive's candidates
        # actually address. The directive names a file and a ``reason``, then
        # points at ``include=['refactoring']`` for the fix — but no detector
        # emits a plan for ``coverage_gradient``, which is the dominant cause on
        # most of this repo's worst files, so that promise was unkeepable and
        # silent about it. Read for the three named files only (``fix_first``
        # plus the two in ``then``), and only when the directive survives the
        # projection, so ``only=["directive"]`` stays the cheapest useful call.
        # Two columns, not whole rows: this reads one field, and the ORM row
        # carries ``plan_json`` + ``evidence_json`` + ``blast_radius_json``.
        # ``status == "open"`` mirrors ``get_refactoring_suggestions`` so the
        # directive cannot claim a plan the ``refactoring`` block would not
        # return. Candidate paths come from ``by_leverage`` ⊆ ``all_metrics``,
        # already exclude-filtered, so the ``IN`` needs no second pass through
        # the exclude spec.
        plan_biomarkers_by_path: dict[str, set[str]] = {}
        plan_count_by_path: dict[str, int] = {}
        if not scoped and wants("directive") and by_leverage:
            directive_paths = [m.file_path for m in by_leverage[:_DIRECTIVE_CANDIDATES]]
            for path, source in (
                await session.execute(
                    select(
                        RefactoringSuggestion.file_path,
                        RefactoringSuggestion.source_biomarker,
                    ).where(
                        RefactoringSuggestion.repository_id == repository.id,
                        RefactoringSuggestion.status == "open",
                        RefactoringSuggestion.file_path.in_(directive_paths),
                    )
                )
            ).all():
                # Presence is counted separately from attribution. Every
                # ``split_file`` and ``break_cycle`` plan stores an empty
                # ``source_biomarker``, so keying "has plans" off the biomarker
                # set would report no plans on a file while the highest-leverage
                # plan kind sits on it.
                plan_count_by_path[path] = plan_count_by_path.get(path, 0) + 1
                if source:
                    plan_biomarkers_by_path.setdefault(path, set()).add(source)

    # KPIs deliberately keep test files in. Excluding them is not a display
    # choice, it is a scoring change: measured across this workspace, dropping
    # test material moves NLOC-weighted ``average_health`` 7.52 -> 6.87 here,
    # 7.07 -> 6.27 on the backend repo and 7.59 -> 7.46 on the frontend. Test
    # files score *better* than production code, so excluding them would make
    # every repo's headline drop overnight with no defect having been found.
    # The calibrated numbers stay where they are; the split above is about which
    # findings compete for a ranked list, not about what the score means.
    kpis = _compute_kpis(
        metric_rows if scoped else all_metrics,
        performance_findings=perf_findings_count,
        coverage=perf_coverage,
    )

    if scoped:
        metric_payload: list[dict[str, Any]] = []
        for m in metric_rows:
            row = _serialize_metric(m, leads.get(m.file_path), is_test=m.file_path in test_paths)
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
            "directive": _directive(
                by_leverage,
                leads,
                gap.get("weighted_gap_points") or 0,
                plan_biomarkers_by_path,
                plan_count_by_path,
            ),
            "kpis": kpis,
            "distribution": health_distribution(all_metrics),
            # Where the gap to Healthy concentrates — the "few files, not the
            # long tail" reframe that turns a repo-wide number into a short list.
            "gap_analysis": gap,
            "worst_files": [
                _serialize_metric(m, leads.get(m.file_path), is_test=m.file_path in test_paths)
                for m in metric_rows[:limit]
            ],
            # Both ranked file lists deliberately keep test files in place, and
            # both now say which rows are tests. Measured on this repo, 0 of the
            # top 25 by the worst-first comparator are test material, so there
            # is no crowding here to fix — and dropping them would quietly
            # change which files the repo's "worst" are. The crowding is in the
            # *finding* lists, which is where the split below happens.
            "worst_files_total": len(metric_rows),
            "high_leverage_files": [
                _serialize_metric(m, leads.get(m.file_path), is_test=m.file_path in test_paths)
                for m in by_leverage[:limit]
            ],
            "high_leverage_files_total": len(by_leverage),
            "top_findings": [_serialize_finding(f) for f in finding_rows[:limit]],
            "top_findings_total": findings_total,
            # The test half of the same ranked set, in its own bucket so a
            # thrashing test suite stays visible without competing with
            # production defect risk for the most-read list.
            "test_findings": [_serialize_finding(f) for f in test_finding_rows[:limit]],
            "test_findings_total": test_findings_total,
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
        # Same production/test split as ``top_findings``: this block only ever
        # fires in dashboard mode (targeted mode set ``findings`` above), so it
        # is describing the repo, not a file the caller named.
        result["test_findings"] = [_serialize_finding(f) for f in test_finding_rows[:limit]]
        result["test_findings_total"] = test_findings_total

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
        # A "plans matching the file's lead biomarker sort first" tiebreak was
        # tried here and removed: measured on this repo, 0 of the top 20 files
        # by deficit have any plan addressing their lead, so it could not move a
        # row inside the cap, while ``deficit`` rounds to an int and ties across
        # files — which would have let the boost reorder *between* files. The
        # honest fix for the mismatch is ``directive.plan_addresses_reason``,
        # which reports it rather than reshuffling around it.
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
        if scoped:
            coverage_payload = [_serialize_coverage_row(r) for r in coverage_rows]
        else:
            # Built narrow, not built wide and subtracted from. These rows came
            # back without the column at all (see the read above).
            coverage_payload = [
                _serialize_coverage_row(r, covered_lines=False) for r in coverage_rows[:limit]
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
    if "refactoring" in include_set and wants("suggestion_legend"):
        # Built from the ranked rows themselves, not from the serialized blocks
        # in ``result``. It used to read ``result["findings"]`` /
        # ``["top_findings"]``, which the ``only`` projection's ``wants()``
        # gating can skip building — so
        # ``only=["refactoring_plans","suggestion_legend"]`` returned an empty
        # legend and adding ``top_findings`` back to ``only`` refilled it. A
        # projection is supposed to subtract keys, never change what a surviving
        # key contains.
        #
        # Scope note, and it is a real limitation rather than an oversight: the
        # legend explains the *findings*, while it ships beside
        # ``refactoring_plans``. Those are different sets — no plan kind is
        # sourced from ``coverage_gradient``, the lead biomarker on this repo's
        # ten worst files — so a legend entry can describe a biomarker the plans
        # do not address. ``directive.plan_addresses_reason`` is what reports
        # that mismatch; the legend is not the place to paper over it.
        present_types = {getattr(r, "biomarker_type", None) for r in legend_rows}
        result["suggestion_legend"] = {
            bt: suggestion_for(bt) for bt in sorted(t for t in present_types if t)
        }

    # Projection. ``include`` could only ever add blocks, so asking for one
    # extra block re-shipped the whole dashboard with it; ``only`` is the
    # subtract half. Applied last so it can drop anything above, and ``mode`` /
    # ``_meta`` always survive — a response the caller cannot orient in is not
    # a saving.
    if only:
        # Every capped list's ``*_total`` sibling survives with it. The tool
        # documents "each carries a ``*_total`` sibling so truncation is never
        # silent", and the projection was quietly breaking exactly that promise:
        # ``only=["modules"]`` at ``limit=50`` returned 50 of 116 modules with
        # no ``modules_total`` to say so. Retaining it is not the caller's job —
        # a caller who knew to ask for the total would not need the guarantee.
        keep = set(only_list) | {"mode"} | {f"{k}_total" for k in only_list}
        # A key that does not exist in this response is named rather than
        # quietly yielding an empty one — same rule as ``unresolved`` above.
        # A misspelled projection is otherwise indistinguishable from a block
        # the repo genuinely has no data for. Reported against what the caller
        # actually passed, so an alias resolving to a present key is not "unknown".
        unknown = sorted(
            raw for raw, resolved in zip(only, only_list, strict=True) if resolved not in result
        )
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
