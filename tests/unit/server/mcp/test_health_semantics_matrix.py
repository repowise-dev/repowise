"""Sealed semantic oracles for the public ``get_health`` recipes.

The table is intentionally data-only: implementation tests consume these exact
calls and expectations without changing the oracle when a behavior fails.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Literal

import pytest

PlanReason = Literal[
    "no_applicable_findings",
    "plan_analysis_indeterminate",
    "no_eligible_targets",
    "analysis_unavailable",
]


@dataclass(frozen=True)
class HealthSemanticsCase:
    """One independently measured health call and its semantic comparison."""

    name: str
    call: str
    kwargs: MappingProxyType[str, Any]
    comparison: str
    comparison_kwargs: MappingProxyType[str, Any]
    plans_requested: bool
    expected_plan_count: int | None
    expected_empty_reason: PlanReason | None
    next_action: str | None
    recovery: Literal["none", "cursor", "omission_ref"]
    invariant_paths: tuple[str, ...]


def _frozen(**kwargs: Any) -> MappingProxyType[str, Any]:
    return MappingProxyType(kwargs)


SEALED_HEALTH_SEMANTICS: tuple[HealthSemanticsCase, ...] = (
    HealthSemanticsCase(
        name="repository directive",
        call='get_health(only=["directive"], limit=0)',
        kwargs=_frozen(only=("directive",), limit=0),
        comparison="default dashboard and limit=50",
        comparison_kwargs=_frozen(limit=50),
        plans_requested=False,
        expected_plan_count=None,
        expected_empty_reason=None,
        next_action=None,
        recovery="none",
        invariant_paths=("directive", "_meta.health_analysis", "_meta.health_semantics"),
    ),
    HealthSemanticsCase(
        name="healthy file self-check",
        call=(
            'get_health(targets=["src/db/models.py"], include=["refactoring"], '
            'only=["metrics", "findings", "refactoring_plans"])'
        ),
        kwargs=_frozen(
            targets=("src/db/models.py",),
            include=("refactoring",),
            only=("metrics", "findings", "refactoring_plans"),
        ),
        comparison="broad healthy-file refactoring call",
        comparison_kwargs=_frozen(
            targets=("src/db/models.py",), include=("refactoring",)
        ),
        plans_requested=True,
        expected_plan_count=0,
        expected_empty_reason="no_applicable_findings",
        next_action=None,
        recovery="none",
        invariant_paths=(
            "metrics",
            "findings",
            "refactoring_plans",
            "_meta.health_analysis",
            "_meta.health_semantics",
        ),
    ),
    HealthSemanticsCase(
        name="unhealthy file self-check",
        call=(
            'get_health(targets=["src/auth/service.py"], include=["refactoring"], '
            'only=["metrics", "findings", "refactoring_plans"])'
        ),
        kwargs=_frozen(
            targets=("src/auth/service.py",),
            include=("refactoring",),
            only=("metrics", "findings", "refactoring_plans"),
        ),
        comparison="broad unhealthy-file refactoring call and repeated ID resolution",
        comparison_kwargs=_frozen(
            targets=("src/auth/service.py",), include=("refactoring",)
        ),
        plans_requested=True,
        expected_plan_count=1,
        expected_empty_reason=None,
        next_action=None,
        recovery="none",
        invariant_paths=(
            "metrics",
            "findings",
            "refactoring_plans",
            "_meta.health_analysis",
            "_meta.health_semantics",
        ),
    ),
    HealthSemanticsCase(
        name="module triage",
        call=(
            'get_health(targets=["module:auth", "module:db"], '
            'only=["modules", "metrics"], limit=1)'
        ),
        kwargs=_frozen(
            targets=("module:auth", "module:db"),
            only=("modules", "metrics"),
            limit=1,
        ),
        comparison="same module with limit=3",
        comparison_kwargs=_frozen(
            targets=("module:auth", "module:db"),
            only=("modules", "metrics"),
            limit=3,
        ),
        plans_requested=False,
        expected_plan_count=None,
        expected_empty_reason=None,
        next_action=None,
        recovery="cursor",
        invariant_paths=(
            "modules",
            "modules_total",
            "metrics_total",
            "_meta.health_analysis",
            "_meta.health_semantics",
        ),
    ),
    HealthSemanticsCase(
        name="trend",
        call='get_health(include=["trend"], only=["trend"])',
        kwargs=_frozen(include=("trend",), only=("trend",)),
        comparison="broad trend expansion",
        comparison_kwargs=_frozen(include=("trend",)),
        plans_requested=False,
        expected_plan_count=None,
        expected_empty_reason=None,
        next_action=None,
        recovery="none",
        invariant_paths=("trend", "_meta.health_analysis", "_meta.health_semantics"),
    ),
    HealthSemanticsCase(
        name="accuracy",
        call='get_health(include=["accuracy"], only=["accuracy"])',
        kwargs=_frozen(include=("accuracy",), only=("accuracy",)),
        comparison="broad accuracy expansion",
        comparison_kwargs=_frozen(include=("accuracy",)),
        plans_requested=False,
        expected_plan_count=None,
        expected_empty_reason=None,
        next_action=None,
        recovery="none",
        invariant_paths=(
            "defect_accuracy",
            "_meta.health_analysis",
            "_meta.health_semantics",
        ),
    ),
    HealthSemanticsCase(
        name="coverage",
        call='get_health(include=["coverage"], only=["coverage"], limit=1)',
        kwargs=_frozen(include=("coverage",), only=("coverage",), limit=1),
        comparison="coverage expansion with a larger page",
        comparison_kwargs=_frozen(
            include=("coverage",), only=("coverage",), limit=20
        ),
        plans_requested=False,
        expected_plan_count=None,
        expected_empty_reason=None,
        next_action=None,
        recovery="cursor",
        invariant_paths=(
            "coverage.summary",
            "coverage.files_total",
            "_meta.health_analysis",
            "_meta.health_semantics",
        ),
    ),
    HealthSemanticsCase(
        name="performance plus refactoring",
        call=(
            'get_health(include=["performance", "refactoring"], '
            'only=["performance_opportunities", "refactoring_plans"], limit=1)'
        ),
        kwargs=_frozen(
            include=("performance", "refactoring"),
            only=("performance_opportunities", "refactoring_plans"),
            limit=1,
        ),
        comparison="independent performance and refactoring projections",
        comparison_kwargs=_frozen(
            include=("performance", "refactoring"), limit=1
        ),
        plans_requested=True,
        expected_plan_count=1,
        expected_empty_reason=None,
        next_action=None,
        recovery="cursor",
        invariant_paths=(
            "performance_opportunities",
            "performance_opportunities_total",
            "refactoring_plans",
            "refactoring_plans_total",
            "_meta.health_analysis",
            "_meta.health_semantics",
        ),
    ),
    HealthSemanticsCase(
        name="findings without supported plan",
        call=(
            'get_health(targets=["src/auth/service.py"], include=["refactoring"], '
            'only=["findings", "refactoring_plans"])'
        ),
        kwargs=_frozen(
            targets=("src/auth/service.py",),
            include=("refactoring",),
            only=("findings", "refactoring_plans"),
        ),
        comparison="same source population through the broad target call",
        comparison_kwargs=_frozen(
            targets=("src/auth/service.py",), include=("refactoring",)
        ),
        plans_requested=True,
        expected_plan_count=0,
        expected_empty_reason="plan_analysis_indeterminate",
        next_action="get_symbol",
        recovery="none",
        invariant_paths=(
            "findings",
            "refactoring_plans",
            "_meta.health_analysis",
            "_meta.health_semantics",
        ),
    ),
    HealthSemanticsCase(
        name="unresolved target",
        call='get_health(targets=["does/not/exist.py"], only=["metrics"])',
        kwargs=_frozen(targets=("does/not/exist.py",), only=("metrics",)),
        comparison="broad unresolved-target call",
        comparison_kwargs=_frozen(targets=("does/not/exist.py",)),
        plans_requested=False,
        expected_plan_count=None,
        expected_empty_reason=None,
        next_action=None,
        recovery="none",
        invariant_paths=("targets", "unresolved", "_meta.health_semantics"),
    ),
    HealthSemanticsCase(
        name="stale indexed analysis with live source",
        call='get_health(targets=["src/auth/service.py"], only=["metrics"])',
        kwargs=_frozen(targets=("src/auth/service.py",), only=("metrics",)),
        comparison="broad target call over the same stale stored row",
        comparison_kwargs=_frozen(targets=("src/auth/service.py",)),
        plans_requested=False,
        expected_plan_count=None,
        expected_empty_reason=None,
        next_action="repowise update",
        recovery="none",
        invariant_paths=("metrics", "_meta.health_analysis", "_meta.health_semantics"),
    ),
    HealthSemanticsCase(
        name="degraded or missing analysis",
        call=(
            'get_health(include=["refactoring"], '
            'only=["directive", "kpis", "refactoring_plans"])'
        ),
        kwargs=_frozen(
            include=("refactoring",),
            only=("directive", "kpis", "refactoring_plans"),
        ),
        comparison="missing-analysis narrow projection",
        comparison_kwargs=_frozen(only=("directive", "kpis")),
        plans_requested=True,
        expected_plan_count=0,
        expected_empty_reason="analysis_unavailable",
        next_action="repowise update",
        recovery="none",
        invariant_paths=("directive", "kpis", "_meta.health_analysis", "_meta.health_semantics"),
    ),
)


def test_sealed_health_semantics_matrix_has_every_independent_oracle() -> None:
    assert tuple(case.name for case in SEALED_HEALTH_SEMANTICS) == (
        "repository directive",
        "healthy file self-check",
        "unhealthy file self-check",
        "module triage",
        "trend",
        "accuracy",
        "coverage",
        "performance plus refactoring",
        "findings without supported plan",
        "unresolved target",
        "stale indexed analysis with live source",
        "degraded or missing analysis",
    )


def test_sealed_health_semantics_matrix_records_required_measurements() -> None:
    for case in SEALED_HEALTH_SEMANTICS:
        assert case.call.startswith("get_health(")
        assert case.comparison
        assert case.invariant_paths
        if case.expected_empty_reason is not None:
            assert case.plans_requested
            assert case.expected_plan_count == 0
        if not case.plans_requested:
            assert case.expected_plan_count is None
            assert case.expected_empty_reason is None


def _assert_counted(result: dict[str, Any], key: str) -> None:
    rows = result[key]
    assert result[f"{key}_total"] >= result[f"{key}_emitted"] == len(rows)


def _mutable_kwargs(values: MappingProxyType[str, Any]) -> dict[str, Any]:
    return {key: list(value) if isinstance(value, tuple) else value for key, value in values.items()}


def _path(result: dict[str, Any], dotted: str) -> Any:
    value: Any = result
    for part in dotted.split("."):
        value = value[part]
    return value


async def _seed_case_evidence(case: HealthSemanticsCase, session, repository_id: str) -> None:
    from repowise.core.persistence.crud import (
        save_coverage_files,
        save_health_findings,
        save_health_snapshot,
        save_refactoring_suggestions,
    )

    if case.name == "coverage":
        await save_coverage_files(
            session,
            repository_id,
            [
                {
                    "file_path": path,
                    "line_coverage_pct": pct,
                    "covered_lines": [1],
                    "total_coverable_lines": 10,
                    "branch_coverage_pct": pct,
                }
                for path, pct in (("src/auth/service.py", 20.0), ("src/db/models.py", 90.0))
            ],
            source_format="lcov",
        )
    if case.name == "trend":
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for day, scores in (
            (0, {"src/auth/service.py": 4.0}),
            (1, {"src/auth/service.py": 4.5}),
        ):
            await save_health_snapshot(
                session,
                repository_id,
                hotspot_health=4.5,
                average_health=5.3,
                worst_performer_path="src/auth/service.py",
                worst_performer_score=4.5,
                per_file_scores=scores,
                taken_at=base + timedelta(days=day),
            )
    if case.expected_plan_count:
        plans = [
            {
                "refactoring_type": "extract_method",
                "file_path": "src/auth/service.py",
                "target_symbol": "authenticate",
                "line_start": 10,
                "line_end": 80,
                "plan": {"groups": []},
                "evidence": {},
                "impact_delta": 1.2,
                "effort_bucket": "S",
                "blast_radius": {"dependents_count": 0},
                "confidence": "high",
                "source_biomarker": "complex_method",
            }
        ]
        if case.name == "performance plus refactoring":
            await save_health_findings(
                session,
                repository_id,
                [
                    {
                        "file_path": "src/db/models.py",
                        "biomarker_type": "io_in_loop",
                        "severity": "medium",
                        "function_name": "load_rows",
                        "line_start": 10,
                        "line_end": 10,
                        "details": {"boundary_kind": "db"},
                        "health_impact": 0.5,
                        "reason": "database call inside a loop",
                        "dimension": "performance",
                    }
                ],
            )
            plans.append({**plans[0], "file_path": "src/db/models.py", "target_symbol": "load_rows"})
        await save_refactoring_suggestions(session, repository_id, plans)
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [case for case in SEALED_HEALTH_SEMANTICS if case.name != "degraded or missing analysis"],
    ids=lambda case: case.name,
)
async def test_each_sealed_row_executes_its_call_and_comparison(
    case, setup_mcp, health_data, session
):
    from repowise.server.mcp_server import get_health, tool_middleware

    await _seed_case_evidence(case, session, health_data)
    call = tool_middleware(get_health)
    result = await call(**_mutable_kwargs(case.kwargs))
    comparison = await call(**_mutable_kwargs(case.comparison_kwargs))

    assert result["_meta"]["response_budget"]["serialized_chars"] == len(
        json.dumps(result, separators=(",", ":"), default=str)
    )
    assert result["_meta"]["health_analysis"]["recomputed_this_call"] is False
    assert result["_meta"]["health_analysis"]["live_verification"][
        "source_bytes_verified"
    ] is False
    for dotted in case.invariant_paths:
        if dotted == "metrics" and case.name == "module triage":
            assert _path(result, dotted) == _path(comparison, dotted)[:1]
        else:
            assert _path(result, dotted) == _path(comparison, dotted)
    if case.plans_requested:
        assert len(result["refactoring_plans"]) == case.expected_plan_count
        reason = result["refactoring_plans_status"]["reason"]
        assert reason == case.expected_empty_reason
    if case.recovery == "cursor":
        assert result["recovery"]


@pytest.mark.asyncio
async def test_sealed_missing_analysis_row_executes_without_fabricating_health(
    setup_mcp, populated_db
):
    from repowise.server.mcp_server import get_health, tool_middleware

    case = SEALED_HEALTH_SEMANTICS[-1]
    call = tool_middleware(get_health)
    result = await call(**_mutable_kwargs(case.kwargs))
    comparison = await call(**_mutable_kwargs(case.comparison_kwargs))

    assert result["directive"] is None
    assert result["kpis"]["average_health"] is None
    assert result["kpis"]["analysis_status"] == "unavailable"
    assert result["refactoring_plans"] == []
    assert result["refactoring_plans_status"]["reason"] == case.expected_empty_reason
    assert result["_meta"]["health_analysis"]["status"] == "unavailable"
    assert comparison["_meta"]["health_analysis"] == result["_meta"]["health_analysis"]
    assert result["_meta"]["response_budget"]["serialized_chars"] == len(
        json.dumps(result, separators=(",", ":"), default=str)
    )



@pytest.mark.asyncio
async def test_sealed_projection_invariance_uses_independent_calls(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    dashboard = await get_health()
    directive = await get_health(only=["directive"], limit=0)
    assert directive["directive"] == dashboard["directive"]
    assert directive["_meta"]["health_semantics"] == dashboard["_meta"]["health_semantics"]
    assert directive["_meta"]["health_analysis"] == dashboard["_meta"]["health_analysis"]

    broad = await get_health(
        targets=["src/auth/service.py"], include=["refactoring"], limit=2
    )
    narrow = await get_health(
        targets=["src/auth/service.py"],
        include=["refactoring"],
        only=["metrics", "findings", "refactoring_plans"],
        limit=2,
    )
    for key in ("metrics", "findings", "refactoring_plans"):
        assert narrow[key] == broad[key]
        assert narrow[f"{key}_total"] == broad[f"{key}_total"]
        assert narrow[f"{key}_emitted"] == broad[f"{key}_emitted"]
        _assert_counted(narrow, key)
    assert narrow["refactoring_plans_status"] == broad["refactoring_plans_status"]
    assert narrow.get("unresolved", []) == []
    assert narrow["_meta"]["health_analysis"] == broad["_meta"]["health_analysis"]


@pytest.mark.asyncio
async def test_module_limit_changes_page_not_rollup_or_row_meaning(setup_mcp, health_data):
    from repowise.server.mcp_server import get_health

    short = await get_health(
        targets=["module:auth", "module:db"],
        only=["modules", "metrics"],
        limit=1,
    )
    long = await get_health(
        targets=["module:auth", "module:db"],
        only=["modules", "metrics"],
        limit=3,
    )
    assert short["modules"] == long["modules"]
    assert short["modules_total"] == long["modules_total"] == 2
    assert short["metrics_total"] == long["metrics_total"] == 2
    assert short["metrics"] == long["metrics"][:1]
    assert [row["file_path"] for row in short["metrics"]] == ["src/auth/service.py"]
    assert short["_meta"]["health_analysis"] == long["_meta"]["health_analysis"]


@pytest.mark.asyncio
async def test_seven_registry_health_recipes_are_bounded_and_self_describing(
    setup_mcp, health_data
):
    from repowise.server.mcp_server import get_health, tool_middleware

    call = tool_middleware(get_health)
    recipes = (
        ({"only": ["directive"]}, "directive"),
        ({"targets": ["src/auth/service.py"], "include": ["refactoring"]}, "metrics"),
        (
            {"targets": ["module:auth"], "only": ["modules", "metrics"]},
            "modules",
        ),
        ({"include": ["trend"], "only": ["trend"]}, "trend"),
        ({"include": ["accuracy"], "only": ["accuracy"]}, "defect_accuracy"),
        ({"include": ["coverage"], "only": ["coverage"]}, "coverage"),
        (
            {
                "include": ["performance", "refactoring"],
                "only": ["performance_opportunities", "refactoring_plans"],
            },
            "performance_opportunities",
        ),
    )
    for kwargs, answer_key in recipes:
        result = await call(**kwargs)
        assert answer_key in result
        assert result["_meta"]["health_semantics"]
        assert result["_meta"]["health_analysis"]
        size = len(json.dumps(result, separators=(",", ":"), default=str))
        budget = 32_000 if kwargs.get("include") else 24_000
        assert size <= budget
