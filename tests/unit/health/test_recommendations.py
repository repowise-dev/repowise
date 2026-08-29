"""Recommendation rank and validation contract fixtures."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.analysis.health.coverage import TestCoverage
from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.recommendations import (
    apply_view,
    build_recommendations,
    build_validation_plan,
    detector_native_benefit,
    hydrate_recommendations,
    rehydrate_suggestion,
)
from repowise.core.analysis.test_reachability import ReachedBy
from repowise.core.persistence.crud import save_test_coverage
from repowise.core.persistence.database import init_db
from tests.unit.persistence.helpers import insert_repo


def _plan(
    target: str,
    *,
    rtype: str = "extract_class",
    file_path: str = "src/core.py",
    impact: float = 2.0,
    blast: int = 0,
    evidence: dict | None = None,
) -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type=rtype,
        file_path=file_path,
        target_symbol=target,
        line_start=10,
        line_end=20,
        plan={},
        evidence=evidence or {},
        impact_delta=impact,
        effort_bucket="M",
        blast_radius={"file_count": blast},
        confidence="high",
        source_biomarker="long_function",
    )


def test_larger_blast_radius_increases_risk_not_benefit() -> None:
    narrow, wide = build_recommendations([_plan("narrow", blast=1), _plan("wide", blast=30)])
    by_target = {item.suggestion.target_symbol: item for item in (narrow, wide)}
    assert by_target["wide"].risk > by_target["narrow"].risk
    assert by_target["wide"].benefit == by_target["narrow"].benefit
    assert by_target["wide"].rank_score < by_target["narrow"].rank_score


def test_performance_fix_uses_detector_native_benefit_at_zero_health_impact() -> None:
    plan = _plan(
        "sink",
        rtype="performance_fix",
        impact=0.0,
        evidence={"rank_score": 24, "provenance": "call-site"},
    )
    recommendation = build_recommendations([plan])[0]
    assert recommendation.benefit > 0
    assert recommendation.rank_score > 0


def test_performance_benefit_excludes_call_site_blast_factor() -> None:
    common = {"multiplier_shape": 3, "boundary_kind": 2, "provenance": 1}
    narrow = _plan(
        "narrow",
        rtype="performance_fix",
        impact=0.0,
        blast=1,
        evidence={"rank_factors": {**common, "affected_call_sites": 1}},
    )
    wide = _plan(
        "wide",
        rtype="performance_fix",
        impact=0.0,
        blast=30,
        evidence={"rank_factors": {**common, "affected_call_sites": 8}},
    )
    recommendations = build_recommendations([narrow, wide])
    by_target = {item.suggestion.target_symbol: item for item in recommendations}
    assert by_target["wide"].benefit == by_target["narrow"].benefit
    assert by_target["wide"].risk > by_target["narrow"].risk


def test_zero_health_score_is_not_treated_as_healthy() -> None:
    plan = _plan("worst")
    recommendation = build_recommendations(
        [plan], metric_by_path={"src/core.py": SimpleNamespace(nloc=100, score=0.0)}
    )[0]
    assert recommendation.file_weighted_deficit == 800
    assert recommendation.leverage > 1.0


def test_default_order_is_deterministic() -> None:
    plans = [_plan("B"), _plan("A"), _plan("C", impact=1.0)]
    forward = [item.suggestion.target_symbol for item in build_recommendations(plans)]
    reverse = [
        item.suggestion.target_symbol for item in build_recommendations(list(reversed(plans)))
    ]
    assert forward == reverse == ["A", "B", "C"]


def test_legacy_persisted_row_rehydrates_without_phase3_fields() -> None:
    suggestion = rehydrate_suggestion(
        {
            "id": "legacy",
            "refactoring_type": "extract_method",
            "file_path": "src/legacy.py",
            "target_symbol": "run",
            "plan_json": '{"span":{"start":1,"end":5}}',
            "evidence_json": "{}",
            "blast_radius_json": "{}",
            "impact_delta": 1.0,
            "effort_bucket": "M",
            "confidence": "medium",
        }
    )
    recommendation = build_recommendations([suggestion])[0]
    assert recommendation.id == "legacy"
    assert recommendation.validation.basis == "unknown"
    assert recommendation.as_dict()["risk"] > 0


def test_named_spread_view_does_not_redefine_canonical_priority() -> None:
    plans = [
        _plan("A1", file_path="a.py", impact=4),
        _plan("A2", file_path="a.py", impact=3),
        _plan("B1", file_path="b.py", impact=2),
    ]
    canonical = build_recommendations(plans)
    spread = apply_view(canonical, "file_spread")
    assert [item.suggestion.target_symbol for item in canonical] == ["A1", "A2", "B1"]
    assert [item.suggestion.target_symbol for item in spread] == ["A1", "B1", "A2"]
    assert {item.suggestion.target_symbol: item.rank_score for item in canonical} == {
        item.suggestion.target_symbol: item.rank_score for item in spread
    }


def test_measured_coverage_suppresses_inferred_evidence_for_same_target() -> None:
    plan = _plan("covered")
    measured = {
        "src/core.py": [
            {
                "test_id": "tests/test_core.py::test_measured",
                "test_file": "tests/test_core.py",
                "covered_lines": [12],
                "source_format": "coverage.py",
            }
        ]
    }
    inferred = {"src/core.py": ReachedBy(["tests/test_inferred.py"], "call-graph", 1)}
    validation = build_validation_plan(plan, measured, inferred)
    assert validation.basis == "measured"
    assert validation.via == "coverage"
    assert validation.tests == ["tests/test_core.py::test_measured"]


def test_call_graph_evidence_precedes_import_fallback_by_target() -> None:
    plan = _plan("mixed", file_path="src/a.py")
    plan.blast_radius = {"files": ["src/b.py"]}
    inferred = {
        "src/a.py": ReachedBy(["tests/test_a.py"], "call-graph", 1),
        "src/b.py": ReachedBy(["tests/test_b.py"], "import-graph", 1),
    }
    validation = build_validation_plan(plan, {}, inferred)
    assert [target.via for target in validation.targets] == ["call-graph", "import-graph"]
    assert validation.basis == "inferred"
    assert validation.via == "mixed"


def test_capped_test_list_keeps_true_total_and_stable_order() -> None:
    plan = _plan("capped")
    reached = ReachedBy(["tests/z.py", "tests/a.py"], "call-graph", 9)
    validation = build_validation_plan(plan, {}, {"src/core.py": reached}, test_limit=1)
    assert validation.total == 9
    assert validation.tests == ["tests/a.py"]
    assert validation.truncated is True
    assert validation.targets[0].total == 9


def test_aggregate_validation_total_deduplicates_tests_across_targets() -> None:
    plan = _plan("shared", file_path="src/a.py")
    plan.blast_radius = {"files": ["src/b.py"]}
    shared = "tests/test_shared.py"
    inferred = {
        "src/a.py": ReachedBy([shared], "call-graph", 1, (shared,)),
        "src/b.py": ReachedBy([shared], "call-graph", 1, (shared,)),
    }
    validation = build_validation_plan(plan, {}, inferred)
    assert validation.total == 1
    assert validation.tests == [shared]
    assert validation.truncated is False


async def test_query_count_is_constant_as_plan_and_test_counts_grow() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async_session = factory()
    repo = await insert_repo(async_session)
    records = [
        TestCoverage(
            test_id=f"tests/test_all.py::test_{index}",
            file_path=f"src/f{index}.py",
            covered_lines=[10],
            source_format="coverage.py",
            test_file="tests/test_all.py",
        )
        for index in range(12)
    ]
    await save_test_coverage(async_session, repo.id, records[:1], source_format="coverage.py")
    await async_session.commit()

    statements: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        statements.clear()
        await hydrate_recommendations(async_session, repo.id, [_plan("one", file_path="src/f0.py")])
        small = len(statements)
        await save_test_coverage(async_session, repo.id, records, source_format="coverage.py")
        await async_session.commit()
        statements.clear()
        await hydrate_recommendations(
            async_session,
            repo.id,
            [_plan(str(index), file_path=f"src/f{index}.py") for index in range(12)],
        )
        large = len(statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
        await async_session.close()
        await engine.dispose()
    assert small == large


def _multi_site_plan() -> RefactoringSuggestion:
    """A ``performance_fix`` whose call sites all sit in one file (the N+1 shape)."""
    return RefactoringSuggestion(
        refactoring_type="performance_fix",
        file_path="svc/orders.py",
        target_symbol="load_all",
        line_start=None,
        line_end=None,
        plan={
            "affected_locations": [
                {"file_path": "svc/orders.py", "line_start": 10, "line_end": 12},
                {"file_path": "svc/orders.py", "line_start": 40, "line_end": 42},
                {"file_path": "svc/orders.py", "line_start": 90, "line_end": 92},
            ]
        },
        evidence={},
        impact_delta=0.0,
        effort_bucket="M",
        blast_radius={},
        confidence="high",
        source_biomarker="",
    )


def test_every_call_site_in_one_file_keeps_its_lines() -> None:
    """Locations accumulate per file; the last one must not evict the others.

    A test covering only the first call site still proves the plan is exercised.
    While locations overwrote, that coverage stopped intersecting, the plan
    reported ``unknown`` with no tests, and its rank fell by the unknown-basis
    risk penalty.
    """
    measured = {
        "svc/orders.py": [
            {"test_id": "tests/test_orders.py::test_first_site", "covered_lines": [10, 11, 12]}
        ]
    }
    plan = build_validation_plan(_multi_site_plan(), measured, {})
    assert plan.basis == "measured"
    assert plan.via == "coverage"
    assert plan.tests == ["tests/test_orders.py::test_first_site"]


def test_a_middle_call_site_is_evidence_too() -> None:
    """Guards the union rather than a first-wins rule that would also pass above."""
    measured = {
        "svc/orders.py": [
            {"test_id": "tests/test_orders.py::test_middle_site", "covered_lines": [40, 41, 42]}
        ]
    }
    assert build_validation_plan(_multi_site_plan(), measured, {}).basis == "measured"


def test_uncovered_lines_in_a_multi_site_plan_stay_unknown() -> None:
    """The union must not turn into 'any row on the file counts'."""
    measured = {
        "svc/orders.py": [
            {"test_id": "tests/test_orders.py::test_elsewhere", "covered_lines": [500, 501]}
        ]
    }
    assert build_validation_plan(_multi_site_plan(), measured, {}).basis == "unknown"


def test_a_truncated_test_list_widens_the_command_past_the_shown_tests() -> None:
    """A capped list must not produce a command that looks like a full run.

    Enumerating only the displayed tests reads as "this validates the change"
    while skipping most of the evidence, so the command falls back to the files
    those tests live in — never narrower than what the plan claims.
    """
    reached = ReachedBy(
        via="call-graph",
        total=40,
        tests=[f"tests/test_orders.py::test_{index}" for index in range(40)],
        all_tests=[f"tests/test_orders.py::test_{index}" for index in range(40)],
    )
    plan = build_validation_plan(_multi_site_plan(), {}, {"svc/orders.py": reached}, test_limit=3)
    assert plan.truncated is True
    assert len(plan.tests) == 3
    assert plan.commands == ["pytest tests/test_orders.py"]


def test_an_untruncated_test_list_keeps_the_precise_command() -> None:
    reached = ReachedBy(
        via="call-graph",
        total=2,
        tests=["tests/test_orders.py::test_a", "tests/test_orders.py::test_b"],
        all_tests=["tests/test_orders.py::test_a", "tests/test_orders.py::test_b"],
    )
    plan = build_validation_plan(_multi_site_plan(), {}, {"svc/orders.py": reached}, test_limit=12)
    assert plan.truncated is False
    assert plan.commands == ["pytest tests/test_orders.py::test_a tests/test_orders.py::test_b"]


# ---- R1 ranking contract -------------------------------------------------


def test_blast_radius_is_charged_once() -> None:
    narrow, wide = build_recommendations([_plan("narrow", blast=1), _plan("wide", blast=30)])
    by_target = {item.suggestion.target_symbol: item for item in (narrow, wide)}
    # Surface moves risk and only risk; effort alone sets cost.
    assert by_target["wide"].cost == by_target["narrow"].cost
    assert by_target["wide"].risk > by_target["narrow"].risk


def test_zero_benefit_plan_cannot_outrank_a_health_recovering_one() -> None:
    # The zero-impact clone sits in the far more popular, far sicker file.
    clone = _plan("clone", rtype="extract_helper", file_path="src/hot.py", impact=0.0)
    real = _plan("real", rtype="extract_method", file_path="src/cold.py", impact=1.5)
    items = build_recommendations(
        [clone, real],
        metric_by_path={
            "src/hot.py": SimpleNamespace(nloc=4000, score=0.0),
            "src/cold.py": SimpleNamespace(nloc=40, score=9.0),
        },
        centrality={"src/hot.py": 300.0, "src/cold.py": 0.0},
    )
    by_target = {item.suggestion.target_symbol: item for item in items}
    assert by_target["clone"].benefit == 0.0
    assert by_target["clone"].rank_score == 0.0
    assert by_target["real"].rank_score > by_target["clone"].rank_score
    assert [item.suggestion.target_symbol for item in items] == ["real", "clone"]


def test_performance_fix_benefit_stays_detector_native() -> None:
    evidence = {"rank_factors": {"loop_depth": 2.0, "affected_call_sites": 40.0}}
    plan = _plan("perf", rtype="performance_fix", impact=0.0, evidence=evidence)
    (item,) = build_recommendations([plan])
    native = detector_native_benefit(rehydrate_suggestion(plan))
    assert item.benefit == round(native, 4)
    assert item.benefit > 0.0
