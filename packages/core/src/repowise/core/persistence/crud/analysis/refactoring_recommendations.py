"""Plan recommendations read from the store: rank, validate and order every plan.

The ranking and validation rules are pure and live in
``analysis/health/refactoring/recommendations.py``; this module runs the reads
they need. Every read is batched across the complete plan set, so adding plans
never adds SQL statements.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.recommendations import (
    DEFAULT_TEST_LIMIT,
    Recommendation,
    RecommendationView,
    SymbolSpan,
    ValidationEvidence,
    ValidationInputs,
    ValidationPlan,
    _expand_scopes,
    _line_ranges,
    _measured_labels,
    _siblings,
    affected_files,
    apply_view,
    build_recommendations,
    build_validation_plan,
    hub_files,
    rehydrate_suggestion,
    target_symbol_ids,
    with_step_verify,
)
from repowise.core.analysis.test_collection import narrow_scopes
from repowise.core.analysis.test_reachability import (
    cached_test_files,
    imported_names_by_test,
    reach_into_symbols,
    tests_matching_by_name,
    tests_reaching_by_tier,
)

from ...models import GraphMetric, GraphNode, HealthFileMetric
from ..graph import get_graph_metrics
from .coverage_map import tests_covering_files
from .health import get_health_metrics


async def hydrate_recommendations(
    session: AsyncSession,
    repository_id: str,
    rows: Sequence[Any],
    *,
    metric_rows: Sequence[Any] | None = None,
    view: RecommendationView = "canonical",
    test_limit: int = DEFAULT_TEST_LIMIT,
    step_verify: bool = False,
) -> list[Recommendation]:
    """Hydrate, enrich, validate, rank, and serialize-ready all *rows*.

    Query shape is constant in plan/test count: health metrics and graph metrics
    are bulk reads, measured coverage is one ``IN`` query, and inferred walks
    use their existing bounded level queries over the complete unanswered set.

    *step_verify* also validates each step of a multi-step plan
    (:func:`with_step_verify`). Finalize asks, so it is stored once; a live
    read never rebuilds it and serves the plan-level answer.
    """
    if not rows:
        return []
    suggestions = [rehydrate_suggestion(row) for row in rows]
    metrics = (
        list(metric_rows)
        if metric_rows is not None
        else await get_health_metrics(session, repository_id)
    )
    graph_metrics = await get_graph_metrics(session, repository_id)
    centrality = {
        node_id: float(metric.get("in_degree") or 0.0) for node_id, metric in graph_metrics.items()
    }
    plans = await _validation_plans(
        session,
        repository_id,
        suggestions,
        test_limit=test_limit,
        in_degree=centrality,
        step_verify=step_verify,
    )
    recommendations = build_recommendations(
        suggestions,
        metric_by_path={metric.file_path: metric for metric in metrics},
        centrality=centrality,
        validations=dict(enumerate(plans)),
    )
    return apply_view(recommendations, view)


async def _validation_plans(
    session: AsyncSession,
    repository_id: str,
    suggestions: Sequence[RefactoringSuggestion],
    *,
    test_limit: int,
    in_degree: Mapping[str, float] | None = None,
    step_verify: bool = False,
) -> list[ValidationPlan]:
    """One validation plan per suggestion, every read batched across the set."""
    target_files = sorted(
        {path for suggestion in suggestions for path in affected_files(suggestion)}
    )
    inputs = await validation_inputs(
        session, repository_id, suggestions, target_files, in_degree=in_degree
    )
    evidence = await _validation_evidence(
        session, repository_id, suggestions, target_files, inputs.test_files, inputs.evidence
    )
    plans = [
        build_validation_plan(
            suggestion,
            inputs.measured,
            inputs.inferred,
            test_limit=test_limit,
            evidence=evidence,
        )
        for suggestion in suggestions
    ]
    if step_verify:
        plans = [
            with_step_verify(
                plan,
                suggestion,
                inputs.measured,
                inputs.inferred,
                test_limit=test_limit,
                evidence=evidence,
            )
            for plan, suggestion in zip(plans, suggestions, strict=True)
        ]
    return plans


async def validation_inputs(
    session: AsyncSession,
    repository_id: str,
    suggestions: Sequence[RefactoringSuggestion],
    target_files: Sequence[str],
    *,
    in_degree: Mapping[str, float] | None = None,
) -> ValidationInputs:
    """Measured coverage and the tiered reachability walk for every plan's files.

    *in_degree* is the stored fan-in a caller already read; without it, read here.
    """
    measured = await tests_covering_files(session, repository_id, set(target_files))

    # A measured row only answers a target when it intersects the plan's line
    # range (if one exists).  Seed inference once with every file that remains
    # unanswered for at least one plan.
    unanswered: set[str] = set()
    for suggestion in suggestions:
        for file_path, lines in _line_ranges(suggestion).items():
            if not _measured_labels(measured.get(file_path, []), lines):
                unanswered.add(file_path)
    test_files = await cached_test_files(session, repository_id)
    if in_degree is None:
        in_degree = {
            node: float(metric.get("in_degree") or 0.0)
            for node, metric in (await get_graph_metrics(session, repository_id)).items()
        }
    # A test that reaches the file only through a hub tests the hub; it is not
    # listed, and with nothing else the plan says no test reaches it.
    hubs = hub_files(in_degree, test_files)
    inferred = (
        await tests_reaching_by_tier(
            session, repository_id, sorted(unanswered), test_files=test_files, avoid=hubs
        )
        if unanswered
        else {}
    )
    unreached = sorted(unanswered - inferred.keys())
    if unreached:
        inferred.update(tests_matching_by_name(unreached, test_files))
    walked = {path: reached.all_tests or tuple(reached.tests) for path, reached in inferred.items()}
    narrowed = await narrow_scopes(session, repository_id, walked, test_files)
    inferred = {
        path: _expand_scopes(path, reached, narrowed[path]) for path, reached in inferred.items()
    }
    hub_targets = [path for path in target_files if path in hubs]
    evidence = await _validation_evidence(
        session,
        repository_id,
        [item for item in suggestions if hubs.intersection(affected_files(item))],
        hub_targets,
        test_files,
        ValidationEvidence(hubs=hubs, siblings=_siblings(in_degree)),
    )
    return ValidationInputs(
        measured=measured, inferred=inferred, test_files=test_files, evidence=evidence
    )


async def _validation_evidence(
    session: AsyncSession,
    repository_id: str,
    suggestions: Sequence[RefactoringSuggestion],
    target_files: Sequence[str],
    test_files: set[str],
    base: ValidationEvidence,
) -> ValidationEvidence:
    """Symbol spans, symbol-level reach and test imports for every plan at once.

    Bounded reads over the whole plan set (the symbol walk is one ``IN`` query
    per hop), so adding plans adds no statements.
    """
    if not target_files:
        return base
    rows = await session.execute(
        select(GraphNode.file_path, GraphNode.node_id, GraphNode.start_line, GraphNode.end_line)
        .where(GraphNode.repository_id == repository_id)
        .where(GraphNode.node_type == "symbol")
        .where(GraphNode.file_path.in_(list(target_files)))
    )
    spans: dict[str, list[SymbolSpan]] = {}
    for file_path, node_id, start, end in rows:
        spans.setdefault(file_path, []).append((node_id, int(start or 0), int(end or 0)))
    symbol_ids = {
        symbol
        for suggestion in suggestions
        for file_path, lines in _line_ranges(suggestion).items()
        for symbol in target_symbol_ids(suggestion, file_path, lines, spans.get(file_path, ()))
    }
    return dataclasses.replace(
        base,
        symbols=spans,
        symbol_reach=await reach_into_symbols(
            session, repository_id, symbol_ids, test_files, avoid=base.hubs
        ),
        imports=await imported_names_by_test(session, repository_id, target_files, test_files),
    )


async def plan_rank_inputs(
    session: AsyncSession, repository_id: str, file_path: str
) -> tuple[dict[str, Any], dict[str, float]]:
    """The two rank inputs for one file, as seeks rather than repo reads.

    ``build_recommendations`` wants a metric per path and an in-degree per
    node. Serving one plan used to load every metric row and every graph
    metric in the repository to supply them for a single file.
    """
    metric = (
        await session.execute(
            select(HealthFileMetric).where(
                HealthFileMetric.repository_id == repository_id,
                HealthFileMetric.file_path == file_path,
            )
        )
    ).scalar_one_or_none()
    rows = (
        await session.execute(
            select(GraphMetric.node_id, GraphMetric.in_degree).where(
                GraphMetric.repository_id == repository_id,
                or_(
                    GraphMetric.node_id == file_path,
                    # The separator matters: a bare ``f"{file_path}%"`` also
                    # matches a sibling whose name extends this one, so
                    # ``Component.ts`` would absorb ``Component.tsx``.
                    GraphMetric.node_id.like(f"{file_path}::%"),
                ),
            )
        )
    ).all()
    return (
        {metric.file_path: metric} if metric is not None else {},
        {node_id: float(in_degree or 0.0) for node_id, in_degree in rows},
    )


__all__ = ["hydrate_recommendations", "plan_rank_inputs", "validation_inputs"]
