"""MCP tool: get_health — code-health markers and per-file scores."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

from sqlalchemy import func, select

from repowise.core.analysis.health.churn_complexity import churn_complexity_points
from repowise.core.analysis.health.complexity.languages import LANGUAGE_MAPS
from repowise.core.analysis.health.coverage import decay_since, measurement_ref
from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy
from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.analysis.health.grading import HEALTHY_MIN, band_for
from repowise.core.analysis.health.grading import distribution as health_distribution
from repowise.core.analysis.health.models import primary_finding
from repowise.core.analysis.health.perf.coverage import PerfCoverage, coverage_for_metrics
from repowise.core.analysis.health.perf.opportunity_rank import observation_rank
from repowise.core.analysis.health.refactoring.recommendations import (
    Recommendation,
    build_recommendations,
    hydrate_recommendations,
)
from repowise.core.analysis.health.scoring import hotspot_health
from repowise.core.analysis.health.semantics import health_semantics_contract
from repowise.core.analysis.health.signals import file_signals
from repowise.core.analysis.health.suggestions import suggestion_for
from repowise.core.analysis.health.trends import diff_snapshots, file_trend, recent_kpis
from repowise.core.ingestion.models import FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.persistence.crud import (
    get_all_git_metadata,
    get_coverage_summary,
    get_file_language_map,
    get_git_metadata_bulk,
    get_health_finding_by_public_id,
    get_hotspot_file_paths,
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
from repowise.core.registry import ToolRecipe
from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    filter_rows_by_attr,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._references import (
    path_identity,
    refactoring_plan_id,
    stable_entity_id,
)
from repowise.server.services.performance_health import (
    PerformanceHealthService,
    PerformancePage,
    evidence_block,
    parse_query,
)
from repowise.server.services.refactoring_health import CANONICAL_VIEWS as _REFACTORING_VIEWS
from repowise.server.services.refactoring_health import DEFAULT_VIEW as _REFACTORING_VIEW_DEFAULT
from repowise.server.services.refactoring_health import (
    RefactoringHealthService,
    plan_view,
)
from repowise.server.services.refactoring_health import (
    parse_query as parse_refactoring_query,
)

# The opportunity id space is shared with the performance pillar's, and told
# apart by prefix alone, so ``opportunity_id`` stays one selector.
_REFACTORING_OPPORTUNITY_PREFIX = "refop"

_REFACTORING_COLLECTION_CAP = 6
"""Opportunities per response, independent of ``limit``. ``cursor`` pages it."""

_REFACTORING_STEP_CAP = 3
"""Steps per row in the queue. The detail call pages the rest."""

_REFACTORING_STEP_PAGE_CAP = 20
"""Ceiling on one page of a detail call's ordered steps."""

_REFACTORING_EVIDENCE_CAP = 3
"""Evidence rows beside a detail response. ``only=['refactoring_evidence']`` pages more."""

_REFACTORING_EVIDENCE_PAGE_CAP = 20
"""Ceiling on one evidence page."""

_PERFORMANCE_COLLECTION_CAP = 6
"""Opportunities per response, independent of ``limit``. ``cursor`` pages it."""

_PERFORMANCE_EVIDENCE_CAP = 3
"""Evidence rows per opportunity in the collection. The detail call pages more."""

_PERFORMANCE_EVIDENCE_PAGE_CAP = 20
"""Ceiling on one evidence page. ``limit`` still means what it says below it,
including ``limit=0`` for the totals and no rows."""


def _selector_conflict(**selectors: str | None) -> dict[str, Any] | None:
    """Refuse two detail selectors instead of answering about one of them.

    Preferring whichever was checked first gave a caller a confident answer to
    a question they had not only asked, with no sign the other was dropped.
    """
    named = sorted(name for name, value in selectors.items() if value)
    if len(named) < 2:
        return None
    return {
        "mode": "conflict",
        "resolved": False,
        "reason": "mutually_exclusive_selectors",
        "selectors": named,
        "detail": "Pass exactly one of finding_id, plan_id, opportunity_id.",
    }


@dataclass(frozen=True, slots=True)
class _PerformanceBlocks:
    """Everything the performance pillar contributes to one response."""

    page: PerformancePage | None = None
    summary: dict[str, Any] | None = None
    directive: dict[str, Any] | None = None
    ignored: dict[str, str] = field(default_factory=dict)


async def _performance_blocks(
    service: PerformanceHealthService,
    *,
    wants: Any,
    included: bool,
    file_paths: tuple[str, ...] | None,
    scoped: bool,
    limit: int,
    cursor: int,
    view: str | None,
    context: str | None,
    boundary: str | None,
    confidence: str | None,
    sort: str | None,
) -> _PerformanceBlocks:
    """Read the materialized queue, its rollup, and the dashboard lead.

    Each block is gated on surviving the projection, so a caller that asked for
    one of the three does not pay for the other two.
    """
    page = None
    query = None
    ignored: dict[str, str] = {}
    if included:
        # The lede quotes only the first row and no evidence, so a projection
        # down to it reads one row rather than a page of six.
        emits_queue = wants("performance_opportunities")
        query, ignored = parse_query(
            context=context,
            boundary=boundary,
            confidence=confidence,
            view=view,
            sort=sort,
            file_paths=file_paths,
            limit=min(max(limit, 0), _PERFORMANCE_COLLECTION_CAP) if emits_queue else 1,
            offset=cursor if emits_queue else 0,
        )
        page = await service.page(
            query,
            evidence_per_item=_PERFORMANCE_EVIDENCE_CAP if emits_queue else 0,
            # Facets are rendered by the summary block alone, so a queue or a
            # lede does not pay for the aggregate.
            with_facets=wants("performance_summary"),
        )
    return _PerformanceBlocks(
        page=page,
        summary=(
            # Scoped to the same context as the queue beside it, so two blocks
            # in one answer cannot state totals that contradict each other.
            await service.summary(query.contexts if query else None)
            if included and wants("performance_summary")
            else None
        ),
        # The bare dashboard lead: one primary-key read of the current summary
        # row, so it does not grow with the repository and never touches the
        # queue.
        directive=(
            await service.directive() if not scoped and wants("performance_directive") else None
        ),
        ignored=ignored,
    )


async def _resolve_finding(
    session: Any, repository_id: str, finding_id: str, repository: str
) -> Any:
    """Find one health finding by any id form a caller can be holding.

    The public id is a column, so the common case is a seek. The scan is the
    compatibility path: a raw storage id, or an id minted before the column
    existed, still has to resolve.
    """
    match = await get_health_finding_by_public_id(session, repository_id, finding_id)
    if match is not None:
        return match
    rows = (
        (
            await session.execute(
                select(HealthFinding).where(
                    HealthFinding.repository_id == repository_id,
                    HealthFinding.status == "open",
                )
            )
        )
        .scalars()
        .all()
    )
    return next(
        (
            row
            for row in rows
            if finding_id
            in {
                row.id,
                _health_finding_id(row, repository),
                _legacy_health_finding_id(row, repository),
            }
        ),
        None,
    )


@dataclass(frozen=True, slots=True)
class _RefactoringBlocks:
    """Everything the refactoring pillar contributes to one response."""

    page: Any = None
    summary: dict[str, Any] | None = None
    directive: dict[str, Any] | None = None
    ignored: dict[str, str] = field(default_factory=dict)


async def _refactoring_blocks(
    service: RefactoringHealthService,
    *,
    wants: Any,
    included: bool,
    file_paths: tuple[str, ...] | None,
    scoped: bool,
    limit: int,
    cursor: int,
    view: str,
    lead_type: str | None = None,
    confidence: str | None = None,
    effort: str | None = None,
) -> _RefactoringBlocks:
    """Read the materialized queue, its rollup, and the dashboard lead.

    Each block is gated on surviving the projection, so a caller that asked for
    one of the three does not pay for the other two.
    """
    page = None
    ignored: dict[str, str] = {}
    if included:
        emits_queue = wants("refactoring_opportunities")
        query, ignored = parse_refactoring_query(
            view=view,
            lead_type=lead_type,
            confidence=confidence,
            effort=effort,
            file_paths=list(file_paths) if file_paths else None,
            limit=min(max(limit, 0), _REFACTORING_COLLECTION_CAP) if emits_queue else 1,
            offset=cursor if emits_queue else 0,
        )
        page = await service.page(
            query,
            steps_per_item=_REFACTORING_STEP_CAP if emits_queue else 0,
            with_facets=wants("refactoring_summary"),
        )
    return _RefactoringBlocks(
        page=page,
        summary=await service.summary() if included and wants("refactoring_summary") else None,
        # The dashboard lead only. A targeted call is already about a file the
        # caller named, so pointing it at the repository's worst file elsewhere
        # would be answering a question nobody asked.
        directive=(
            await service.directive()
            if not scoped and wants("refactoring_directive")
            else None
        ),
        ignored=ignored,
    )


async def _performance_detail_response(
    session: Any,
    repository: Any,
    reference_repository: str,
    opportunity_id: str,
    *,
    evidence_only: bool,
    limit: int,
    cursor: int,
) -> dict[str, Any]:
    """One causal opportunity, or one page of the evidence behind it.

    ``limit`` means what it means everywhere else, ``0`` included: reading it
    as "unset" would return a page the caller declined.
    """
    service = PerformanceHealthService(session, repository.id, reference_repository)
    evidence_limit = min(max(limit, 0), _PERFORMANCE_EVIDENCE_PAGE_CAP)
    if evidence_only:
        rows, total = await service.evidence(
            opportunity_id, limit=evidence_limit, offset=cursor
        )
        return {
            "mode": "performance_evidence",
            "opportunity_id": opportunity_id,
            "resolved": total > 0,
            **evidence_block(rows, total, cursor),
            "_meta": _build_meta(repository=repository),
        }
    detail = await service.detail(opportunity_id, evidence_limit=evidence_limit)
    file_path = detail.get("file_path")
    result = {
        "mode": "performance_opportunity",
        **detail,
        "_meta": _build_meta(
            repository=repository, targets=[file_path] if file_path else None
        ),
    }
    await _attach_repository_analysis_meta(session, repository, result["_meta"])
    return result


async def _refactoring_detail_response(
    session: Any,
    repository: Any,
    reference_repository: str,
    opportunity_id: str,
    *,
    evidence_only: bool,
    limit: int,
    cursor: int,
) -> dict[str, Any]:
    """One composed opportunity, or one page of the evidence behind it."""
    service = RefactoringHealthService(session, repository.id, reference_repository)
    if evidence_only:
        detail = await service.detail(
            opportunity_id,
            step_limit=0,
            evidence_limit=min(max(limit, 0), _REFACTORING_EVIDENCE_PAGE_CAP),
            evidence_offset=cursor,
            with_plans=False,
        )
        block = {k: v for k, v in detail.items() if k.startswith("evidence")}
        result = {
            "mode": "refactoring_evidence",
            "opportunity_id": opportunity_id,
            "resolved": bool(detail.get("resolved")),
            **block,
            "_meta": _build_meta(repository=repository),
        }
        if block.get("evidence_next_cursor") is not None:
            result["recovery"] = {
                "evidence": {
                    "remaining": block["evidence_total"] - block["evidence_next_cursor"],
                    "call": (
                        f"get_health(opportunity_id={opportunity_id!r}, "
                        f"only=['refactoring_evidence'], limit={limit}, "
                        f"cursor={block['evidence_next_cursor']})"
                    ),
                }
            }
        return result
    detail = await service.detail(
        opportunity_id,
        step_limit=min(max(limit, 0), _REFACTORING_STEP_PAGE_CAP),
        step_offset=cursor,
        evidence_limit=_REFACTORING_EVIDENCE_CAP,
    )
    file_path = detail.get("file_path")
    if not detail.get("resolved"):
        detail.setdefault(
            "model_state", _refactoring_model_state(opportunity_id)
        )
    result = {
        "mode": "refactoring_opportunity",
        **detail,
        "_meta": _build_meta(
            repository=repository, targets=[file_path] if file_path else None
        ),
    }
    await _attach_repository_analysis_meta(session, repository, result["_meta"])
    return result


def _refactoring_model_state(opportunity_id: str) -> dict[str, Any]:
    """Tell a stale-model id apart from a wrong one, from the string alone."""
    from repowise.core.analysis.health.refactoring.identity import (
        REFACTORING_MODEL_VERSION,
        model_state,
    )

    state = model_state(
        opportunity_id.replace(_REFACTORING_OPPORTUNITY_PREFIX, "refac", 1)
    )
    state["public_id"] = opportunity_id
    state["refactoring_model_version"] = REFACTORING_MODEL_VERSION
    return state


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
        "function_name": f.function_name,
        "line_start": f.line_start,
        "line_end": f.line_end,
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


def _stamp_collection(
    result: dict[str, Any],
    key: str,
    *,
    total: int | None = None,
    reason: str = "limit",
) -> None:
    """Attach complete-population accounting to one emitted collection."""
    rows = result.get(key)
    if not isinstance(rows, list):
        return
    eligible = len(rows) if total is None else total
    emitted = len(rows)
    result[f"{key}_total"] = eligible
    result[f"{key}_emitted"] = emitted
    if emitted < eligible:
        result[f"{key}_reduced_reason"] = reason


def _validation_profile(validation: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Deduplicate a plan's repeated tests, targets, and command material."""
    encoded = json.dumps(validation, sort_keys=True, separators=(",", ":"), default=str)
    profile_id = "validation_" + hashlib.sha256(encoded.encode()).hexdigest()[:16]
    tests = list(validation.get("tests") or [])
    targets = list(validation.get("targets") or [])
    profile = {
        key: value
        for key, value in validation.items()
        if key not in {"tests", "targets", "commands", "truncated"}
    }
    profile.update(
        {
            "id": profile_id,
            "tests": tests,
            "tests_total": int(validation.get("total") or len(tests)),
            "tests_emitted": len(tests),
            "targets": targets,
            "commands": list(validation.get("commands") or []),
            "commands_total": len(validation.get("commands") or []),
            "commands_emitted": len(validation.get("commands") or []),
        }
    )
    if profile["tests_emitted"] < profile["tests_total"]:
        profile["tests_reduced_reason"] = "analysis_source_cap"
    return profile_id, profile


def _stamp_nested_collections(value: Any) -> None:
    """Give every nested list an emitted count and an honest eligible total."""
    if isinstance(value, list):
        for item in value:
            _stamp_nested_collections(item)
        return
    if not isinstance(value, dict):
        return
    for key, child in list(value.items()):
        if isinstance(child, list):
            total_key = f"{key}_total"
            emitted_key = f"{key}_emitted"
            total = int(value.get(total_key, len(child)) or 0)
            value.setdefault(total_key, total)
            value.setdefault(emitted_key, len(child))
            if len(child) < total:
                value.setdefault(f"{key}_reduced_reason", "limit")
        _stamp_nested_collections(child)


_RANKED_DIMENSIONS_DEFAULT = {"defect", "maintainability"}
"""Dimensions the impact-ranked findings list carries when none is asked for."""

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

    The headline prefers the strongest **discrete** finding. A continuous
    biomarker fires on every file carrying its input signal, so on a repo with
    coverage data ``coverage_gradient`` wins the max-impact tiebreak nearly
    everywhere: measured on this repo before the preference, it led 22 of the
    top 50 ``worst_files`` and 14 of the top 50 ``high_leverage_files`` with
    "N% of lines uncovered", which is true and tells a reader nothing about why
    this file rather than any other. The gradient still counts in
    ``total_deduction`` and still leads when it is a file's only finding — it
    just stops crowding out a nameable cause. That selection rule now lives in
    ``analysis.health.models.primary_finding``, so this and the composition
    layer cannot drift about which finding a file leads with.
    """
    by_file: dict[str, list[Any]] = {}
    for f in findings:
        by_file.setdefault(f.file_path, []).append(f)
    leads: dict[str, dict[str, Any]] = {}
    for path, fs in by_file.items():
        primary = primary_finding(fs)
        if primary is None:
            continue
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
        # The unit is score-points x NLOC, which is meaningless on its own — the
        # docstring and ``gap_analysis.weighted_gap_points`` give it a
        # denominator, and every ``high_leverage_files`` row carries the same
        # quantity as ``share_of_repo_gap_pct``.
        "weighted_deficit": round(max(HEALTHY_MIN - m.score, 0.0) * max(m.nloc, 1)),
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


def _attach_coverage_decay(payload: list[dict[str, Any]], rows: list[Any], repo_path: str) -> None:
    """Add a ``decay`` block to each coverage row, in place.

    The stored percentage is a measurement taken at one commit and never
    recomputed, so on a file under active development it can describe code that
    no longer exists. ``decay`` says how much of that measurement still holds:
    ``confirmed`` covered lines are unchanged since the report, ``invalidated``
    ones have moved and are now unknown rather than uncovered.

    Targeted mode only. Dashboard mode declines ``covered_lines_json`` at the
    read (see the load above), and re-reading every blob to compute drift for a
    list nobody drilled into would undo that saving.

    Silent when the measurement cannot be placed in history, when git cannot
    read the range, or when the report predates every commit. A missing block
    means "not checked", which is why it is absent rather than zero: a zero
    would read as a freshness claim.
    """
    if not rows:
        return
    first = rows[0]
    ref = measurement_ref(
        repo_path,
        getattr(first, "ingested_commit_sha", None),
        getattr(first, "ingested_at", None),
    )
    if ref is None:
        return
    covered_by_file = {
        entry["file_path"]: set(entry.get("covered_lines") or [])
        for entry in payload
        if entry.get("covered_lines")
    }
    decays = decay_since(repo_path, ref, covered_by_file)
    if not decays:
        return
    for entry in payload:
        d = decays.get(entry["file_path"])
        if d is None:
            continue
        entry["decay"] = {
            "measured_lines": d.measured,
            "confirmed_lines": d.confirmed,
            "invalidated_lines": d.invalidated,
            "drift_pct": d.drift_pct,
            "stale": d.is_stale,
            "measured_at_commit": ref[:12],
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
        # long tail" argument made concrete for a single file. The denominator
        # is the *gross* deficit of all below-target files (not the net gap,
        # which healthy files cushion): per-file shares are then bounded by
        # 100% and sum to 100% by construction (issue #1437).
        "recovers_weighted_deficit_points": recovers,
        "recovers_points": recovers,
        "recovers_points_compatibility": {
            "deprecated": True,
            "replacement": "recovers_weighted_deficit_points",
            "equivalent_value": True,
        },
        "share_of_repo_gap_pct": (round(100.0 * recovers / gap_points, 1) if gap_points else None),
        "then": [m.file_path for m in by_leverage[1:3]],
        # Projected, not bare. ``include`` adds a block without subtracting the
        # dashboard, and five ranked lists at the default ``limit`` compose: the
        # bare ``include=['refactoring']`` measured 70,776 chars on this repo
        # (refactoring_plans 34%, the other four lists 59%) and simply fails the
        # MCP token cap, so the one call the directive tells an agent to make was
        # the one call it could not complete. ``only`` already exists to fix this
        # (it gates the work as well as the payload); the directive just has to
        # ask for it. Same block, ~24k chars, no dashboard restated.
        "plan_via": "get_health(include=['refactoring'], only=['refactoring_plans'])",
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
                f"No plan addresses {lead_biomarker!r}; this file has no plans. "
                "Use the finding itself."
            )
        out["next_action"] = f"investigate {lead_biomarker}"
    elif addresses:
        out["next_action"] = "inspect matching plan via plan_via"
    return out


def _finding_next_action(finding: Any, repo: str | None) -> dict[str, Any]:
    """Return one concrete source call for a finding without a stored plan."""
    path = str(getattr(finding, "file_path", ""))
    line_start = getattr(finding, "line_start", None)
    line_end = getattr(finding, "line_end", None)
    if path and line_start and line_end:
        arguments: dict[str, Any] = {"symbol_id": f"{path}:{line_start}-{line_end}"}
        if repo:
            arguments["repo"] = repo
        return {"tool": "get_symbol", "arguments": arguments}
    arguments = {"targets": [path], "include": ["skeleton"]}
    if repo:
        arguments["repo"] = repo
    return {"tool": "get_context", "arguments": arguments}


def _refactoring_plans_status(
    *,
    available_plans_total: int,
    plans_emitted: int,
    scoped: bool,
    has_eligible_metrics: bool,
    finding: Any | None,
    repo: str | None,
) -> dict[str, Any]:
    """Explain an explicitly requested plan projection deterministically."""
    if plans_emitted:
        return {"state": "available", "reason": None}
    if available_plans_total:
        return {
            "state": "available_not_emitted",
            "reason": "request_window_empty",
            "message": "Plans exist but the requested limit/cursor window emitted none.",
        }
    if scoped and not has_eligible_metrics:
        return {
            "state": "unavailable",
            "reason": "no_eligible_targets",
            "message": "No requested target resolved to an eligible stored health row.",
        }
    if not has_eligible_metrics:
        return {
            "state": "unavailable",
            "reason": "analysis_unavailable",
            "message": "No stored health analysis is available for this population.",
            "next_action": {
                "command": "repowise update",
                "reason": "run health analysis before interpreting scores or plans",
            },
        }
    if finding is None:
        return {
            "state": "empty",
            "reason": "no_applicable_findings",
            "message": "The eligible population has no applicable open findings.",
        }
    return {
        "state": "indeterminate",
        "reason": "plan_analysis_indeterminate",
        "message": "Findings exist, but stored plan evidence is absent.",
        "possible_causes": [
            "no_supported_structured_transformation",
            "refactoring_detector_disabled_or_failed",
        ],
        "next_action": _finding_next_action(finding, repo),
    }


def _attach_health_analysis_meta(
    meta: dict[str, Any], metrics: list[HealthFileMetric]
) -> None:
    """Keep stored analysis distinct from index/live Git verification."""
    analyzed = [m for m in metrics if getattr(m, "updated_at", None)]
    latest = max(analyzed, key=lambda m: m.updated_at) if analyzed else None
    _write_health_analysis_meta(
        meta,
        has_metrics=bool(metrics),
        latest_at=latest.updated_at if latest is not None else None,
        latest_commit=getattr(latest, "analyzed_commit", None) if latest else None,
        distinct_commits=len({c for m in analyzed if (c := getattr(m, "analyzed_commit", None))}),
    )


async def _attach_repository_analysis_meta(
    session: Any, repository: Any, meta: dict[str, Any]
) -> None:
    """The same block, computed over the repository rather than one file.

    "Has this repository's health analysis recorded its commit" is a fact about
    the analysis, but every detail mode used to answer it from the rows it
    happened to be reporting on. A ``plan_id`` call scoped to a file whose row
    carries a commit said ``available`` at the same instant the dashboard said
    ``degraded (analysis_commit_not_recorded)`` from the repo-wide latest row,
    and the reverse when the scoped file was the one missing it. Three bounded
    aggregates, so agreeing costs no scan.
    """
    latest = (
        await session.execute(
            select(HealthFileMetric.updated_at, HealthFileMetric.analyzed_commit)
            .where(
                HealthFileMetric.repository_id == repository.id,
                HealthFileMetric.updated_at.is_not(None),
            )
            .order_by(HealthFileMetric.updated_at.desc())
            .limit(1)
        )
    ).first()
    total, distinct = (
        await session.execute(
            select(
                func.count(),
                func.count(func.distinct(HealthFileMetric.analyzed_commit)),
            ).where(HealthFileMetric.repository_id == repository.id)
        )
    ).one()
    _write_health_analysis_meta(
        meta,
        has_metrics=total > 0,
        latest_at=latest[0] if latest else None,
        latest_commit=latest[1] if latest else None,
        distinct_commits=distinct,
    )


def _write_health_analysis_meta(
    meta: dict[str, Any],
    *,
    has_metrics: bool,
    latest_at: Any,
    latest_commit: str | None,
    distinct_commits: int,
) -> None:
    """The one place the analysis-freshness block is shaped."""
    meta["health_semantics"] = health_semantics_contract()
    metrics = has_metrics
    analyzed = latest_at is not None
    commits_count = distinct_commits
    status = "available" if latest_commit else "degraded" if metrics else "unavailable"
    analysis: dict[str, Any] = {
        "status": status,
        "source": "stored_health_analysis",
        "recomputed_this_call": False,
        "live_verification": {
            "basis": (
                "index_commit_and_live_git_head"
                if meta.get("indexed_commit") and meta.get("live_head")
                else "unavailable"
            ),
            "source_bytes_verified": False,
        },
        "refresh": {
            "command": "repowise update",
            "precondition": "commit health-relevant working-tree changes first",
            "required_before_comparison": True,
        },
    }
    if not metrics:
        analysis["reason"] = "no_stored_health_metrics"
    if analyzed:
        analyzed_at = latest_at.isoformat()
        analysis["analyzed_at"] = analyzed_at
        meta["health_analyzed_at"] = analyzed_at
        if latest_commit:
            analyzed_commit = latest_commit[:12]
            analysis["analyzed_commit"] = analyzed_commit
            meta["health_analyzed_commit"] = analyzed_commit
        if commits_count > 1:
            analysis["analyzed_commits_distinct"] = commits_count
            meta["health_analyzed_commits_distinct"] = commits_count
        if not latest_commit:
            analysis["reason"] = "analysis_commit_not_recorded"
            analysis["analyzed_commit"] = None
    else:
        analysis["recorded_at"] = None
        analysis["recorded_commit"] = None
        if metrics:
            analysis["reason"] = "analysis_timestamp_not_recorded"
    meta["health_analysis"] = analysis


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
    *few large low-scoring files*, not the long tail. Two gaps are computed and
    kept deliberately distinct:

    - ``weighted_gap_points`` — the **net** points the average needs
      (``8.0 * total_nloc - Σ score*nloc``). Healthy files already sit above 8.0
      and cushion it, so this is smaller than the gross all-files-healthy
      deficit and is the number that matches the goal "move the average".
      ``files_to_reach_target`` is the punchline: lift the worst-deficit N files
      to 8.0 and the headline crosses 8.0. This can be 0 or negative on a
      mostly-healthy repo (the average is already above 8.0).
    - ``weighted_gross_gap_points`` — the **gross** deficit,
      ``Σ max(8.0 - score, 0) * nloc`` over files below 8.0. This is the
      denominator ``share_of_repo_gap_pct`` uses: it is positive whenever any
      file is below target (unlike the net gap), so a share is meaningful even
      when the average is already healthy, and per-file shares sum to exactly
      100% by construction. The net gap is not used there precisely because
      healthy files cushion it — one large low file could then read as closing
      more than the whole remaining gap (issue #1437).

    Pure over the metrics in hand.
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
    gross_gap = sum(below)
    if not below:
        return {
            "target_score": HEALTHY_MIN,
            "weighted_gap_points": 0,
            "weighted_gross_gap_points": 0,
            "files_below_target": 0,
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

    # ``files_to_reach_target`` needs the net gap to mean "the headline crosses
    # 8.0". When the net gap is <= 0 (average already healthy) there is nothing
    # to reach, so those fields are 0 — but the gross gap is still reported
    # because per-file shares are meaningful whenever any file is below target.
    reachable = max(net_gap, 0)
    return {
        "target_score": HEALTHY_MIN,
        # Net weighted points the average must recover to reach 8.0.
        "weighted_gap_points": round(max(net_gap, 0)),
        # Gross deficit of all below-target files: the share_of_repo_gap_pct
        # denominator (see docstring for why it is not the net gap).
        "weighted_gross_gap_points": round(gross_gap),
        "files_below_target": len(below),
        # The reframe: lift this many worst-deficit files to 8.0 and the weighted
        # average reaches 8.0; half that gap needs even fewer.
        "files_to_reach_target": _files_for(reachable),
        "files_for_half_gap": _files_for(0.5 * reachable),
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


def _code_only(
    metrics: list[HealthFileMetric], lang_by_path: dict[str, str]
) -> list[HealthFileMetric]:
    """The metric rows the complexity walker actually walks.

    ``LANGUAGE_MAPS`` is already the repo's definition of "real code" — the perf
    pillar uses exactly this filter so docs/config rows never dilute its
    coverage math (``perf/coverage.py::coverage_for_metrics``). The defect
    headline never applied it.
    """
    return [m for m in metrics if lang_by_path.get(m.file_path, "") in LANGUAGE_MAPS]


def _compute_kpis(
    metrics: list[HealthFileMetric],
    *,
    hotspot_paths: set[str] | None = None,
    performance_findings: int = 0,
    coverage: PerfCoverage | None = None,
    lang_by_path: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not metrics:
        return {
            "file_count": 0,
            "average_health": None,
            "band": None,
            "analysis_status": "unavailable",
            "hotspot_health": None,
            "worst_performer_path": None,
            "worst_performer_score": None,
            "maintainability_average": None,
            "performance_average": None,
            **_perf_kpis(0, None),
        }
    total_nloc = sum(max(m.nloc, 1) for m in metrics)
    avg = sum(m.score * max(m.nloc, 1) for m in metrics) / total_nloc
    worst = min(metrics, key=lambda r: r.score)
    # What the headline would read over code alone. No biomarker walks a
    # markdown or JSON file, so those rows carry a mechanical 10.0 that means
    # "nothing looked at this", exactly the fabricated-10.0 problem the perf
    # pillar already surfaces rather than hides (``perf/coverage.py``). Measured
    # on this repo: 233 of 3,314 rows are non-code, 221 of them score exactly
    # 10.0, they are 7.5% of NLOC, and they lift ``average_health`` 7.31 -> 7.47.
    # Surfaced rather than subtracted: ``average_health`` is what the badge, the
    # snapshots, the trend alerts and the web UI all read, and redefining it
    # here alone would make this tool disagree with every one of them.
    code_kpis: dict[str, Any] = {}
    if lang_by_path is not None:
        code = _code_only(metrics, lang_by_path)
        code_nloc = sum(max(m.nloc, 1) for m in code)
        code_kpis = {
            "non_code_files": len(metrics) - len(code),
            "average_health_code_only": (
                round(sum(m.score * max(m.nloc, 1) for m in code) / code_nloc, 2)
                if code_nloc
                else None
            ),
        }
    return {
        "file_count": len(metrics),
        "average_health": round(avg, 2),
        # ``None`` when the repo has no hotspot files, which is a real answer:
        # the alternative is averaging an empty set to a perfect 10.0 and
        # reporting it as a score, the same fabricated-10.0 problem the comment
        # above objects to for non-code rows.
        "hotspot_health": hotspot_health(metrics, hotspot_paths or set()),
        **code_kpis,
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


@mcp.tool(
    surface_order=90,
    artifact_type="health",
    presentation="health",
    evidence_basis="measured",
    recipes=(
        ToolRecipe(
            "health_directive",
            'get_health(only=["directive"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_file_self_check",
            'get_health(targets=["path"], include=["refactoring"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_module_triage",
            'get_health(targets=["module:path"], only=["modules","metrics"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_trend",
            'get_health(include=["trend"], only=["trend"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_accuracy",
            'get_health(include=["accuracy"], only=["accuracy"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_coverage",
            'get_health(include=["coverage"], only=["coverage"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_performance_refactoring",
            'get_health(include=["performance","refactoring"], '
            'only=["performance_opportunities","refactoring_plans"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_performance_summary",
            'get_health(include=["performance"], only=["performance_summary"])',
            ("get_health",),
        ),
        ToolRecipe(
            "health_performance_opportunity",
            'get_health(opportunity_id="perf...")',
            ("get_health",),
        ),
        ToolRecipe(
            "health_performance_evidence",
            'get_health(opportunity_id="perf...", '
            'only=["performance_evidence"], cursor=0)',
            ("get_health",),
        ),
    ),
)
async def get_health(
    targets: list[str] | None = None,
    include: list[str] | None = None,
    repo: str | None = None,
    limit: int = 20,
    only: list[str] | None = None,
    refactoring_view: str = _REFACTORING_VIEW_DEFAULT,
    refactoring_type: str | None = None,
    refactoring_confidence: str | None = None,
    refactoring_effort: str | None = None,
    cursor: int = 0,
    finding_id: str | None = None,
    plan_id: str | None = None,
    opportunity_id: str | None = None,
    performance_view: str | None = None,
    performance_context: str | None = None,
    performance_boundary: str | None = None,
    performance_confidence: str | None = None,
    performance_sort: str | None = None,
) -> dict:
    """Code-health scores and findings from stored analysis.

    No ``targets`` returns a dashboard; targets return ranked files and findings.
    Never recomputes health: commit changes, then run ``repowise update``.
    Every block and accepted value: docs/agent/MCP_TOOLS.md.

    Args:
        targets: file paths or ``module:<name>``. Empty means dashboard;
            unmatched ones land in ``unresolved``, surviving ``only``.
        include: ``biomarkers`` | ``refactoring`` | ``trend`` | ``coverage`` |
            ``accuracy`` | ``signals`` | ``churn_complexity``, or a dimension.
            ``performance`` and ``refactoring`` add their queues.
        only: keys to keep; identity, counts and recovery survive.
            ``biomarkers``, ``accuracy`` and ``refactoring`` alias their block
            key. ``performance``, ``defect`` and ``maintainability`` do not:
            they filter rows and land in ``unknown_only_keys``.
        repo: usually omitted.
        limit: max rows per ranked list, ``0`` for none.
        cursor: zero-based offset into a ranked list.
        finding_id: stable ``id`` from a health finding.
        plan_id: stable ``id`` from a refactoring plan.
        opportunity_id: ``perf...`` or ``refop...`` id from a directive or
            queue: the unit, its steps or plan, and evidence paged by
            ``only=["*_evidence"]``. Excludes the two ids above.
        refactoring_view: ``diversified`` (default) | ``canonical`` |
            ``file_spread``; refactoring_type / _confidence / _effort filter.
        performance_view / _context / _boundary / _confidence / _sort: queue
            projection and filters; the facets list them.

    """
    started = perf_counter()
    conflict = _selector_conflict(
        finding_id=finding_id, plan_id=plan_id, opportunity_id=opportunity_id
    )
    if conflict is not None:
        return conflict
    # ``0`` means none, matching the ``module_limit`` convention on the REST
    # coverage route. It used to clamp up to 1, so the documented way to ask for
    # "the totals, none of the rows" silently returned a row.
    limit = max(limit, 0)
    cursor = max(cursor, 0)
    if refactoring_view not in _REFACTORING_VIEWS:
        refactoring_view = _REFACTORING_VIEW_DEFAULT
    include_set = set(include or [])
    known_includes = {
        "biomarkers",
        "refactoring",
        "trend",
        "coverage",
        "accuracy",
        "signals",
        "churn_complexity",
        "performance",
        "defect",
        "maintainability",
    }
    unknown_include_keys = sorted(include_set - known_includes)
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
    # The ranked findings list is ordered by health impact, and every
    # performance finding carries zero impact by construction, so leaving it
    # in an unfiltered list appends rows that can never rank and cannot be
    # compared against the ones above them. Asking for the dimension still
    # returns it, and the performance blocks rank the same evidence by cause.
    ranked_dimensions = dimension_filter or _RANKED_DIMENSIONS_DEFAULT
    # The serialized-rows read is the expensive optional one; skip it when no
    # block that carries findings survives the projection.
    wants_findings = wants("findings") or wants("top_findings")
    wants_test_findings = wants("test_findings")
    wants_performance_opportunities = (
        wants("performance_opportunities")
        or wants("recommendation_lede")
        or wants("performance_summary")
    )
    wants_refactoring_opportunities = (
        wants("refactoring_opportunities")
        or wants("recommendation_lede")
        or wants("refactoring_summary")
    )
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
    omission_collector = OmissionCollector("get_health", repo_root=ctx.path)
    semantic_omissions: dict[str, list[Any]] = {}
    page_recoveries: dict[str, tuple[int, int, int]] = {}
    paged_collections = {
        "metrics",
        "findings",
        "trends",
        "worst_files",
        "high_leverage_files",
        "top_findings",
        "test_findings",
        "modules",
        "churn_complexity",
        "coverage.files",
        "refactoring_plans",
        "refactoring_opportunities",
        "refactoring_evidence",
        "performance_opportunities",
        "performance_evidence",
    }

    def bounded(rows: list[Any], label: str, *, cap: int | None = None) -> list[Any]:
        """Bound one collection and retain its exact tail in shared omission storage."""
        row_cap = limit if cap is None else cap
        start = cursor if label in paged_collections else 0
        kept = rows[start : start + row_cap]
        if len(kept) < len(rows):
            tail_start = start + len(kept)
            if label in paged_collections:
                if tail_start < len(rows):
                    next_limit = (
                        min(row_cap or 6, 6)
                        if label
                        in {
                            "refactoring_plans",
                            "refactoring_opportunities",
                            "performance_opportunities",
                        }
                        else min(len(rows) - tail_start, 50)
                    )
                    page_recoveries[label] = (
                        tail_start,
                        next_limit,
                        len(rows) - tail_start,
                    )
                elif rows and start >= len(rows):
                    page_recoveries[label] = (0, min(len(rows), max(row_cap, 1), 50), len(rows))
            else:
                semantic_omissions[label] = rows[row_cap:]
        return kept
    # Performance headline inputs (dashboard mode): filled inside the session.
    perf_coverage: PerfCoverage | None = None
    perf_findings_count = 0
    # ``None`` means "not read", which is what keeps the code/non-code KPI split
    # off targeted responses rather than reporting it over one file.
    lang_by_path: dict[str, str] | None = None
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        reference_repository = ctx.alias or repository.name

        if finding_id:
            match = await _resolve_finding(
                session, repository.id, finding_id, reference_repository
            )
            result = {
                "mode": "finding",
                "finding_id": finding_id,
                "finding": (
                    _serialize_finding(match, reference_repository) if match else None
                ),
                "resolved": match is not None,
                "_meta": _build_meta(
                    repository=repository,
                    targets=[match.file_path] if match else None,
                ),
            }
            await _attach_repository_analysis_meta(session, repository, result["_meta"])
            return result

        if opportunity_id and opportunity_id.startswith(_REFACTORING_OPPORTUNITY_PREFIX):
            return await _refactoring_detail_response(
                session,
                repository,
                reference_repository,
                opportunity_id,
                evidence_only=only_set == {"refactoring_evidence"},
                limit=limit,
                cursor=cursor,
            )

        if opportunity_id:
            return await _performance_detail_response(
                session,
                repository,
                reference_repository,
                opportunity_id,
                evidence_only=only_set == {"performance_evidence"},
                limit=limit,
                cursor=cursor,
            )

        if plan_id:
            # An indexed seek and one hydration, not a full load and a linear
            # scan: resolving one id used to cost every open plan in the repo.
            service = RefactoringHealthService(
                session, repository.id, reference_repository
            )
            resolved = await service.plan_detail(plan_id)
            plan = resolved.get("plan") if resolved.get("resolved") else None
            if plan is not None:
                plan.setdefault("id", plan_id)
                plan["repository"] = reference_repository
            result = {
                "mode": "refactoring_plan",
                "plan_id": plan_id,
                "plan": plan,
                "resolved": bool(resolved.get("resolved")),
                "_meta": _build_meta(
                    repository=repository,
                    targets=[plan["file_path"]] if plan else None,
                ),
            }
            if resolved.get("opportunity_id"):
                result["opportunity_id"] = resolved["opportunity_id"]
                result["next_action"] = resolved["next_action"]
            elif plan is not None:
                result["opportunity_note"] = (
                    "This plan is addressable but is not a step of any composed "
                    "opportunity; a demoted clone is supporting evidence, not work."
                )
            await _attach_repository_analysis_meta(session, repository, result["_meta"])
            return result

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

        # Test material, from the flag ingestion already decided per file.
        # Gated on ``needs_test_paths`` — see the note at its definition — and
        # placed after the ``module:`` expansion so targeted mode can scope it.
        #
        # Targeted mode only ever asks ``path in test_paths`` for paths the
        # caller named, so it reads exactly those; dashboard mode partitions a
        # ranked finding list whose paths are not known until the read below
        # runs, so it keeps the repo-wide answer. Measured on this repo, that
        # is 32.9ms -> 0.6ms on a single-file target — a quarter of the whole
        # call, paid to answer "is this one file a test".
        test_paths: set[str] = set()
        if needs_test_paths:
            test_paths = await get_test_file_paths(
                session, repository.id, effective_targets if scoped else None
            )

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
            emitted = _rank_emitted(
                [f for f in finding_rows if _in_dimensions(f, ranked_dimensions)]
            )
            finding_rows = emitted
            legend_rows: list[Any] = finding_rows
        else:
            # Narrow read over every open finding: the four attributes
            # ``_leads_by_file`` reads, plus ``dimension`` for the perf headline
            # and ``id`` to fetch the head. SQLAlchemy ``Row`` exposes these as
            # attributes, so the reduction and the exclude filter both run
            # against it unchanged.
            #
            # ``details_json`` joins them only when the caller asked for the
            # performance dimension, because that is the only case where a perf
            # finding can reach the head at all: every one carries
            # ``health_impact: 0``, so in a mixed list all ~10k defect findings
            # sort above them and the rank could not move a row. Measured on
            # this repo the column costs 6.6ms on the read (parsing the 697 perf
            # rows out of 10,740 costs a further 1.2ms), which is worth paying
            # for the one call it changes and not worth paying for the default.
            lite_cols = [
                HealthFinding.id,
                HealthFinding.file_path,
                HealthFinding.health_impact,
                HealthFinding.biomarker_type,
                HealthFinding.reason,
                HealthFinding.dimension,
            ]
            if "performance" in dimension_filter:
                lite_cols.append(HealthFinding.details_json)
            if wants_performance_opportunities:
                lite_cols.extend(
                    [
                        HealthFinding.function_name,
                        HealthFinding.line_start,
                        HealthFinding.line_end,
                    ]
                )
            lite_rows = list(
                (
                    await session.execute(
                        select(*lite_cols)
                        .where(*open_findings)
                        .order_by(HealthFinding.health_impact.desc())
                    )
                ).all()
            )
            # ``lead_rows`` stays the unfiltered open set: it feeds the per-file
            # leads and the performance KPI, neither of which should change
            # because the caller asked to *see* one dimension.
            lead_rows = filter_rows_by_attr(lite_rows, "file_path", exclude_spec)
            emitted = _rank_emitted(
                [r for r in lead_rows if _in_dimensions(r, ranked_dimensions)]
            )
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

        action_finding_rows = emitted
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

        # Hotspot health was the one repo KPI this tool never returned, while
        # ``get_overview`` invented its own definition for it — so the canonical
        # persisted number was surfaced by neither. One scalar column, gated the
        # same way as the language map below: ``targets`` mode builds no ``kpis``
        # block at all, so scoping the call must not pay for this read.
        hotspot_paths: set[str] = set()
        if not scoped and wants("kpis"):
            hotspot_paths = await get_hotspot_file_paths(session, repository.id)

        # Dashboard perf headline: coverage (how much of the analyzed code the
        # perf pass ran on) + open performance-finding count. Both feed ``kpis``
        # alone, so a projection that drops kpis skips the language-map read.
        # The same map answers "how much of this headline is non-code" — one
        # read, two KPIs.
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
        refactoring_recommendations: list[Recommendation] = []
        # Only when a caller names the plan list. ``include=["refactoring"]``
        # leads with composed opportunities now, and emitting both would ship
        # two representations of the same work in one response - 52k chars on
        # this repo, past the expanded budget, most of it duplicated. The
        # documented ``only=["refactoring_plans"]`` call is unchanged.
        plans_requested = "refactoring" in include_set and (
            "refactoring_plans" in only_set
            # The cross-pillar lede quotes one plan beside one performance
            # opportunity, and only when both pillars were asked for.
            or ({"performance", "refactoring"} <= include_set and wants("recommendation_lede"))
        )
        if plans_requested and not nothing_resolved:
            refactoring_rows = filter_rows_by_attr(
                await get_refactoring_suggestions(
                    session,
                    repository.id,
                    file_paths=list(effective_targets) if scoped else None,
                ),
                "file_path",
                exclude_spec,
            )
            refactoring_recommendations = await hydrate_recommendations(
                session,
                repository.id,
                refactoring_rows,
                metric_rows=all_metrics,
                view=plan_view(refactoring_view),
            )

        # The materialized causal read model. Filtering, ordering, paging, plan
        # linkage, and facets are the shared service's; this tool caps the
        # collection, pages it, and serializes what comes back.
        performance_service = PerformanceHealthService(
            session, repository.id, reference_repository
        )
        refactoring_service = RefactoringHealthService(
            session, repository.id, reference_repository
        )
        refactoring = await _refactoring_blocks(
            refactoring_service,
            wants=wants,
            included="refactoring" in include_set and wants_refactoring_opportunities,
            file_paths=tuple(effective_targets) if scoped else None,
            scoped=scoped,
            limit=limit,
            cursor=cursor,
            view=refactoring_view,
            lead_type=refactoring_type,
            confidence=refactoring_confidence,
            effort=refactoring_effort,
        )
        performance = await _performance_blocks(
            performance_service,
            wants=wants,
            included="performance" in include_set and wants_performance_opportunities,
            file_paths=tuple(effective_targets) if scoped else None,
            scoped=scoped,
            limit=limit,
            cursor=cursor,
            view=performance_view,
            context=performance_context,
            boundary=performance_boundary,
            confidence=performance_confidence,
            sort=performance_sort,
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
                session,
                repository.id,
                list(effective_targets),
                edge_types=sorted(FILE_DEPENDENCY_EDGE_TYPES),
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
            churn_points = [asdict(p) for p in churn_complexity_points(all_metrics, git_meta_by_path)]

        # Load the snapshot window for the repo-level trend block and/or the
        # per-file trajectory we attach in targeted mode ("should I touch this
        # file" context for agents).
        snapshots: list[Any] = []
        if "trend" in include_set or (scoped and wants("trends")):
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
        hotspot_paths=hotspot_paths,
        performance_findings=perf_findings_count,
        coverage=perf_coverage,
        lang_by_path=lang_by_path,
    )

    if scoped:
        metric_payload: list[dict[str, Any]] = []
        for m in metric_rows:
            row = _serialize_metric(m, leads.get(m.file_path), is_test=m.file_path in test_paths)
            if m.file_path in signals_by_path:
                row["signals"] = signals_by_path[m.file_path]
            metric_payload.append(row)
        module_rollup = _module_rollups(
            [m for m in all_metrics if m.module in set(module_targets)]
        )
        result: dict[str, Any] = {
            "mode": "targets",
            "targets": raw_targets,
            # Deliberately NOT capped by ``limit``: the caller named these files
            # and getting back fewer than they asked about would answer a
            # different question. The response-size guard is what bounds it, and
            # ``metrics_total`` is the ``*_total`` sibling that makes a trim
            # visible — a ``module:`` target expands to every file in the module,
            # so this is the one growable list whose length the caller cannot
            # infer from what they passed.
            **({"modules": module_rollup} if module_targets else {}),
            "metrics": bounded(metric_payload, "metrics"),
            "metrics_total": len(metric_payload),
            # Capped like every other ranked list, with the total alongside so
            # the truncation is visible rather than inferred from the length.
            "findings": bounded(
                [_serialize_finding(f, reference_repository) for f in finding_rows],
                "findings",
            ),
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
            series = [round(p.score, 2) for p in t.points]
            entry: dict[str, Any] = {
                "file_path": t.file_path,
                "series": series,
                "current": t.current,
                "delta": t.delta,
                "declining": t.declining,
            }
            # The score floors at 1.0, so a file deep enough to sit on it keeps
            # a flat series however much of the work gets done. Where a
            # snapshot recorded the real depth, carry the series that can still
            # move — and only there, so the files the floor never touches pay
            # nothing for it.
            unclamped = [round(p.unclamped_score, 2) for p in t.points]
            if unclamped != series:
                entry["unclamped_series"] = unclamped
                entry["unclamped_delta"] = t.unclamped_delta
            trends.append(entry)
        if trends:
            result["trends"] = bounded(trends, "trends")
        if module_targets and not only and not include_set:
            result["secondary_rankings"] = {
                "findings": {
                    "total": findings_total,
                    "call": (
                        f"get_health(targets={raw_targets!r}, only=['findings'], "
                        f"repo={repo!r}, limit=50, refactoring_view='{refactoring_view}')"
                    ),
                },
                "trends": {
                    "total": len(trends),
                    "call": (
                        f"get_health(targets={raw_targets!r}, only=['trends'], "
                        f"repo={repo!r}, limit=50, refactoring_view='{refactoring_view}')"
                    ),
                },
            }
            for block in ("findings", "trends"):
                result.pop(block, None)
                result.pop(f"{block}_total", None)
    else:
        # Dashboard mode — top-N worst files + headline findings + the
        # per-module rollup so the overview page doesn't need a second
        # round-trip. ``by_leverage`` is built above, before the leads.
        # Same serializer as worst_files, so every row carries
        # weighted_deficit for the caller to sort on further.
        all_modules = _module_rollups(all_metrics)
        gap = _gap_analysis(all_metrics)
        result = {
            # Lead with the call, not the data. Every block below ranks and
            # describes; this one recommends.
            "directive": _directive(
                by_leverage,
                leads,
                gap.get("weighted_gross_gap_points") or 0,
                plan_biomarkers_by_path,
                plan_count_by_path,
            ),
            # A second, additive lead for the performance pillar. The block
            # above is unchanged: performance findings carry no defect impact,
            # so they never competed for it and the dashboard said nothing an
            # agent could act on about them.
            # Two more additive leads, on the same terms: the block above ranks
            # files by health deficit and cannot say which composed work to do,
            # and it explicitly reports that no plan addresses the cause it
            # names on most files. These say what there is to do about it.
            **(
                {"refactoring_directive": refactoring.directive}
                if refactoring.directive is not None
                else {}
            ),
            **(
                {"performance_directive": performance.directive}
                if performance.directive is not None
                else {}
            ),
            "mode": "dashboard",
            "kpis": kpis,
            "distribution": health_distribution(all_metrics),
            # Where the gap to Healthy concentrates — the "few files, not the
            # long tail" reframe that turns a repo-wide number into a short list.
            "gap_analysis": gap,
            "worst_files": bounded([
                _serialize_metric(m, leads.get(m.file_path), is_test=m.file_path in test_paths)
                for m in metric_rows
            ], "worst_files"),
            # Both ranked file lists deliberately keep test files in place, and
            # both now say which rows are tests. Measured on this repo, 0 of the
            # top 25 by the worst-first comparator are test material, so there
            # is no crowding here to fix — and dropping them would quietly
            # change which files the repo's "worst" are. The crowding is in the
            # *finding* lists, which is where the split below happens.
            "worst_files_total": len(metric_rows),
            # The one list whose entire purpose is leverage ranking, so it is the
            # one place ``weighted_deficit`` gets a denominator. The bare number
            # is score-points x NLOC and answers "which is bigger" but never "is
            # this worth doing"; the same quantity as a share of the repo's total
            # gap does, and it is the unit ``directive`` already speaks. The
            # denominator is the gross deficit of all below-target files, so a
            # share is bounded by 100% and the rows sum to 100% by construction
            # — the net gap would let healthy files cushion the total and push a
            # single large file over 100% (issue #1437).
            "high_leverage_files": bounded([
                {
                    **_serialize_metric(
                        m, leads.get(m.file_path), is_test=m.file_path in test_paths
                    ),
                    "share_of_repo_gap_pct": (
                        round(
                            100.0
                            * max(HEALTHY_MIN - m.score, 0.0)
                            * max(m.nloc, 1)
                            / gap["weighted_gross_gap_points"],
                            1,
                        )
                        if gap.get("weighted_gross_gap_points")
                        else None
                    ),
                }
                for m in by_leverage
            ], "high_leverage_files"),
            "high_leverage_files_total": len(by_leverage),
            "top_findings": bounded(
                [_serialize_finding(f, reference_repository) for f in finding_rows],
                "top_findings",
            ),
            "top_findings_total": findings_total,
            # The test half of the same ranked set, in its own bucket so a
            # thrashing test suite stays visible without competing with
            # production defect risk for the most-read list.
            "test_findings": bounded(
                [_serialize_finding(f, reference_repository) for f in test_finding_rows],
                "test_findings",
            ),
            "test_findings_total": test_findings_total,
            # Worst-first, so the cap keeps the modules worth looking at. On a
            # monorepo the tail is dozens of single-file buckets.
            "modules": bounded(all_modules, "modules"),
            "modules_total": len(all_modules),
        }
        if not only:
            result["secondary_rankings"] = {
                "worst_files": {
                    "total": len(metric_rows),
                    "call": f"get_health(repo={repo!r}, only=['worst_files'], limit=50)",
                },
                "top_findings": {
                    "total": findings_total,
                    "call": f"get_health(repo={repo!r}, only=['top_findings'], limit=50)",
                },
                "test_findings": {
                    "total": test_findings_total,
                    "call": f"get_health(repo={repo!r}, only=['test_findings'], limit=50)",
                },
                "modules": {
                    "total": len(all_modules),
                    "call": f"get_health(repo={repo!r}, only=['modules'], limit=50)",
                },
            }
            for block in ("worst_files", "top_findings", "test_findings", "modules"):
                result.pop(block, None)
                result.pop(f"{block}_total", None)
        if "churn_complexity" in include_set:
            result["churn_complexity"] = bounded(churn_points, "churn_complexity")
        if "accuracy" in include_set:
            # Self-validation: does the score rank the buggy files first?
            # Scored over the full open set (``accuracy_rows``), not the capped
            # head — ranking quality measured on the top 20 would be circular.
            # ``None`` when there isn't enough signal for an honest number.
            result["defect_accuracy"] = compute_defect_accuracy(
                all_metrics,
                [_serialize_finding(f, reference_repository) for f in accuracy_rows],
            )

    if "biomarkers" in include_set and "findings" not in result:
        # Capped like every other ranked list. Uncapped, this was the one block
        # in the tool that could return the repo's entire open finding set: on a
        # 3.2k-file repo ``include=["biomarkers"]`` with no targets served 10.3k
        # rows / 4.7MB, which overflows an agent's context and returns nothing
        # usable. Findings arrive impact-ordered, so the cap keeps the ones
        # worth reading.
        result["findings"] = bounded(
            [_serialize_finding(f, reference_repository) for f in finding_rows],
            "findings",
        )
        result["findings_total"] = findings_total
        # Same production/test split as ``top_findings``: this block only ever
        # fires in dashboard mode (targeted mode set ``findings`` above), so it
        # is describing the repo, not a file the caller named.
        result["test_findings"] = bounded(
            [_serialize_finding(f, reference_repository) for f in test_finding_rows],
            "test_findings",
        )
        result["test_findings_total"] = test_findings_total

    if plans_requested:
        # Canonical is the shared REST/MCP/CLI order.  File diversity remains
        # available only through the explicitly named ``file_spread`` view.
        validation_profiles: dict[str, dict[str, Any]] = {}
        plan_payload = []
        selected_recommendations = bounded(
            refactoring_recommendations,
            "refactoring_plans",
            cap=min(limit, 6),
        )
        for recommendation in selected_recommendations:
            payload = _serialize_refactoring(recommendation, reference_repository)
            validation = payload.pop("validation", None)
            if validation:
                profile_id, profile = _validation_profile(validation)
                validation_profiles.setdefault(profile_id, profile)
                payload["validation_profile_id"] = profile_id
            plan_payload.append(payload)
        result["refactoring_plans"] = plan_payload
        if validation_profiles:
            result["validation_profiles"] = list(validation_profiles.values())
            _stamp_collection(
                result,
                "validation_profiles",
                total=len(validation_profiles),
                reason="profile_cap",
            )
        result["refactoring_plans_total"] = len(refactoring_rows)
        if wants("refactoring_plans"):
            finding_for_action = next(iter(action_finding_rows), None)
            result["refactoring_plans_status"] = _refactoring_plans_status(
                available_plans_total=len(refactoring_recommendations),
                plans_emitted=len(plan_payload),
                scoped=scoped,
                has_eligible_metrics=bool(metric_rows if scoped else all_metrics),
                finding=finding_for_action,
                repo=repo,
            )
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
        recent = recent_kpis(snapshots, limit=10)
        alerts = [
            {
                "kind": a.kind,
                "metric": a.metric,
                "current": a.current,
                "baseline": a.baseline,
                "delta": a.delta,
                "message": a.message,
            }
            for a in summary.alerts
        ]
        result["trend"] = {
            "current_hotspot_health": summary.current_hotspot_health,
            "current_average_health": summary.current_average_health,
            "previous_hotspot_health": summary.previous_hotspot_health,
            "previous_average_health": summary.previous_average_health,
            "hotspot_delta": summary.hotspot_delta,
            "average_delta": summary.average_delta,
            "alerts": bounded(alerts, "trend.alerts"),
            "alerts_total": len(alerts),
            "alerts_emitted": min(len(alerts), limit),
            "recent": bounded(recent, "trend.recent"),
            "recent_total": len(recent),
            "recent_emitted": min(len(recent), limit),
        }
        if len(alerts) > limit:
            result["trend"]["alerts_reduced_reason"] = "limit"
        if len(recent) > limit:
            result["trend"]["recent_reduced_reason"] = "limit"

    if refactoring.page is not None and wants("refactoring_opportunities"):
        result["refactoring_opportunities"] = refactoring.page.items
        result["refactoring_opportunities_total"] = refactoring.page.total
        result["refactoring_opportunities_emitted"] = len(refactoring.page.items)
        if len(refactoring.page.items) < refactoring.page.total:
            result["refactoring_opportunities_reduced_reason"] = (
                "collection_cap" if limit > _REFACTORING_COLLECTION_CAP else "limit"
            )
        if refactoring.page.next_offset is not None:
            page_recoveries["refactoring_opportunities"] = (
                refactoring.page.next_offset,
                _REFACTORING_COLLECTION_CAP,
                refactoring.page.total - refactoring.page.next_offset,
            )
        if refactoring.ignored:
            result["ignored_arguments"] = {
                **result.get("ignored_arguments", {}),
                **refactoring.ignored,
            }

    if refactoring.summary is not None:
        result["refactoring_summary"] = {
            **refactoring.summary,
            "facets": refactoring.page.facets if refactoring.page else {},
            "view": refactoring_view,
            "next_call": (
                "get_health(include=['refactoring'], "
                "only=['refactoring_opportunities'], limit=6)"
            ),
        }

    if performance.page is not None and wants("performance_opportunities"):
        result["performance_opportunities"] = performance.page.items
        result["performance_opportunities_total"] = performance.page.total
        result["performance_opportunities_emitted"] = len(performance.page.items)
        if performance.page.next_offset is not None:
            page_recoveries["performance_opportunities"] = (
                performance.page.next_offset,
                _PERFORMANCE_COLLECTION_CAP,
                performance.page.total - performance.page.next_offset,
            )
        if performance.ignored:
            result["ignored_arguments"] = {
                **result.get("ignored_arguments", {}),
                **performance.ignored,
            }

    if performance.summary is not None:
        result["performance_summary"] = {
            **performance.summary,
            "facets": performance.page.facets if performance.page else {},
            "next_call": (
                "get_health(include=['performance'], "
                "only=['performance_opportunities'], limit=6)"
            ),
        }

    if {"performance", "refactoring"} <= include_set and wants("recommendation_lede"):
        performance_lead = (
            performance.page.items[0] if performance.page and performance.page.items else None
        )
        recommendation_lead = (
            refactoring_recommendations[0] if refactoring_recommendations else None
        )
        lead_payload = (
            _serialize_refactoring(recommendation_lead, reference_repository)
            if recommendation_lead
            else None
        )
        result["recommendation_lede"] = {
            "performance_opportunities_total": (
                performance.page.total if performance.page else 0
            ),
            "refactoring_plans_total": len(refactoring_recommendations),
            "performance_lead": (
                {
                    key: performance_lead[key]
                    for key in (
                        "opportunity_id",
                        "intervention_symbol",
                        "boundary_kind",
                        "execution_context",
                        "affected_call_sites_total",
                        "rank_score",
                    )
                }
                if performance_lead
                else None
            ),
            "recommendation_lead": (
                {
                    key: lead_payload[key]
                    for key in (
                        "id",
                        "refactoring_type",
                        "file_path",
                        "target_symbol",
                        "benefit",
                        "leverage",
                        "cost",
                        "risk",
                        "rank_score",
                    )
                }
                if lead_payload
                else None
            ),
            # The exact plan for the exact lead, from the one place that decides
            # plan linkage. This used to match on a key the plan writer never
            # wrote, so it was unconditionally null.
            "performance_plan_id": (
                performance_lead["plan_reference"] if performance_lead else None
            ),
            "performance_plan_reason": (
                performance_lead["plan_reason"] if performance_lead else None
            ),
            "next_call": (
                f"get_health(targets={raw_targets!r}, repo={repo!r}, "
                "include=['performance','refactoring'], limit=3, "
                "only=['performance_opportunities','refactoring_plans'], "
                f"refactoring_view='{refactoring_view}')"
            ),
        }

    if "coverage" in include_set:
        # Drop the bulky covered-lines arrays from dashboard mode; full
        # detail is available in targeted mode.
        if scoped:
            selected_coverage = bounded(coverage_rows, "coverage.files")
            coverage_payload = [_serialize_coverage_row(r) for r in selected_coverage]
            _attach_coverage_decay(coverage_payload, selected_coverage, str(ctx.path))
        else:
            # Built narrow, not built wide and subtracted from. These rows came
            # back without the column at all (see the read above).
            full_coverage_payload = [
                _serialize_coverage_row(r, covered_lines=False) for r in coverage_rows
            ]
            coverage_payload = bounded(full_coverage_payload, "coverage.files")
        # ``ingested_at`` is a datetime on the summary too — coerce.
        if coverage_summary.get("ingested_at") is not None:
            coverage_summary = {
                **coverage_summary,
                "ingested_at": coverage_summary["ingested_at"].isoformat(),
            }
        result["coverage"] = {
            "summary": coverage_summary,
            "files": coverage_payload,
            "files_total": len(coverage_rows),
            "files_emitted": len(coverage_payload),
        }
        if len(coverage_payload) < len(coverage_rows):
            result["coverage"]["files_reduced_reason"] = "limit"

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

    collection_totals = {
        "targets": len(raw_targets),
        "metrics": len(metric_payload) if scoped else None,
        "findings": findings_total,
        "trends": len(trends) if scoped else None,
        "modules": len(module_rollup) if scoped else len(all_modules),
        "worst_files": len(metric_rows),
        "high_leverage_files": len(by_leverage),
        "top_findings": findings_total,
        "test_findings": test_findings_total,
        "churn_complexity": len(churn_points),
        "refactoring_plans": len(refactoring_recommendations),
        "performance_opportunities": (
            performance.page.total if performance.page is not None else 0
        ),
    }
    for key, total in collection_totals.items():
        if total is None:
            continue
        cap_reason = (
            "collection_cap"
            if key in {"refactoring_plans", "performance_opportunities"}
            and limit > 6
            and len(result.get(key, [])) == 6
            else "limit"
        )
        _stamp_collection(result, key, total=total, reason=cap_reason)
    if unknown_include_keys:
        result["unknown_include_keys"] = unknown_include_keys
    _stamp_nested_collections(result)

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
        # ``unresolved`` / ``known_modules`` survive any projection, for the same
        # reason ``mode`` does. They are the block that stops an empty result
        # reading as "this file is healthy" (A1), and projecting them away put a
        # typo'd target straight back to silent: ``targets=["does/not/exist.py"],
        # only=["metrics"]`` returned ``metrics: []`` and nothing else. A caller
        # who has to ask for the error report in order to see it does not have an
        # error report.
        keep = (
            set(only_list)
            | {
                "mode",
                "targets",
                "targets_total",
                "targets_emitted",
                "unresolved",
                "unresolved_total",
                "unresolved_emitted",
                "known_modules",
                "known_modules_total",
                "known_modules_emitted",
                "unknown_include_keys",
                "unknown_include_keys_total",
                "unknown_include_keys_emitted",
                # A rejected filter value is a caller-error report, so it
                # survives a projection for the same reason ``unresolved`` does.
                "ignored_arguments",
                "recovery",
            }
            | {
                suffix
                for k in only_list
                for suffix in (
                    f"{k}_total",
                    f"{k}_emitted",
                    f"{k}_reduced_reason",
                )
            }
        )
        if "refactoring_plans" in only_set:
            keep |= {
                "refactoring_plans_status",
                "validation_profiles",
                "validation_profiles_total",
                "validation_profiles_emitted",
                "validation_profiles_reduced_reason",
            }
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
            _stamp_collection(result, "unknown_only_keys", total=len(unknown))

    visible_recoveries = {
        label: values
        for label, values in page_recoveries.items()
        if label.split(".", 1)[0] in result
    }
    if visible_recoveries:
        recovery: dict[str, dict[str, Any]] = {}
        for label, (next_cursor, next_limit, remaining) in visible_recoveries.items():
            root = label.split(".", 1)[0]
            recovery[label] = {
                "remaining": remaining,
                "call": (
                    f"get_health(targets={raw_targets!r}, include={list(include or [])!r}, "
                    f"repo={repo!r}, limit={next_limit}, only={[root]!r}, "
                    f"refactoring_view='{refactoring_view}', cursor={next_cursor})"
                ),
            }
        result["recovery"] = recovery

    # Targeted mode scopes the stale signal to the asked-about files; the
    # dashboard (no targets) keeps the repo-level warning.
    result["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
    analyzed_source = metric_rows if scoped else all_metrics
    if scoped:
        # Scoped calls used to answer repository freshness from the caller's own
        # files, which is how the same repo read ``available`` and ``degraded``
        # in the same second depending on which mode answered.
        await _attach_repository_analysis_meta(session, repository, result["_meta"])
    else:
        _attach_health_analysis_meta(result["_meta"], analyzed_source)
    # Server-side wall clock, as ``get_context`` already reports. Without it a
    # regression in here is invisible until someone profiles it by hand.
    for label, dropped in semantic_omissions.items():
        root = label.split(".", 1)[0]
        if root in result:
            omission_collector.add(
                f"{label} beyond emitted cap ({len(dropped)} dropped)",
                [
                    _serialize_refactoring(row, reference_repository)
                    if isinstance(row, Recommendation)
                    else row.as_dict()
                    if hasattr(row, "as_dict")
                    else row
                    for row in dropped
                ],
            )
    omission_collector.attach(result)
    result["_meta"]["timing_ms"] = round((perf_counter() - started) * 1000, 2)
    return result
