"""Recommendation rank and validation contract fixtures."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.analysis.health.coverage import TestCoverage
from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.recommendations import (
    ValidationEvidence,
    apply_view,
    build_recommendations,
    build_validation_plan,
    detector_native_benefit,
    hub_files,
    hydrate_recommendations,
    rehydrate_suggestion,
    target_symbol_ids,
)
from repowise.core.analysis.test_reachability import ReachDistance, ReachedBy, clear_test_map_cache
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


def test_a_test_file_plan_never_leads_the_canonical_order() -> None:
    plans = [
        _plan("helper", file_path="tests/test_core.py", impact=9.0),
        _plan("worker", impact=1.0),
    ]
    ranked = build_recommendations(plans)
    assert [item.suggestion.target_symbol for item in ranked] == ["worker", "helper"]
    assert ranked[1].rank_score > ranked[0].rank_score


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


def test_the_test_named_for_the_file_survives_the_cap() -> None:
    plan = _plan("named")
    reached = ReachedBy(["tests/a/test_other.py", "tests/unit/test_core.py"], "import-graph", 2)
    validation = build_validation_plan(plan, {}, {"src/core.py": reached}, test_limit=1)
    assert validation.tests == ["tests/unit/test_core.py"]


def test_a_reached_conftest_validates_with_the_tests_under_it() -> None:
    """``pytest tests/unit/conftest.py`` collects nothing; the tests below it run."""
    from repowise.core.analysis.health.refactoring.recommendations import _expand_scopes
    from repowise.core.analysis.test_selection import expand_test_scopes

    test_files = {"tests/unit/conftest.py", "tests/unit/test_core.py", "tests/other/test_x.py"}
    reached = ReachedBy(
        ["tests/unit/conftest.py"], "call-graph", 1, ("tests/unit/conftest.py",)
    )
    validation = build_validation_plan(
        _plan("fixture"), {}, {"src/core.py": _expand_scopes("src/core.py", reached, expand_test_scopes(reached.all_tests, test_files))}
    )
    assert validation.basis == "inferred"
    assert validation.tests == ["tests/unit/test_core.py"]
    assert validation.commands == ["pytest tests/unit/test_core.py"]


def test_a_root_conftest_expansion_is_ranked_before_it_is_capped() -> None:
    """A root conftest stands for every test; the cut keeps the nearest, not the first."""
    from repowise.core.analysis.health.refactoring.recommendations import _expand_scopes
    from repowise.core.analysis.test_reachability import MAX_TESTS_PER_TARGET
    from repowise.core.analysis.test_selection import expand_test_scopes

    many = {f"tests/aaa/test_{i:03}.py" for i in range(MAX_TESTS_PER_TARGET + 20)}
    test_files = {"conftest.py", "tests/unit/test_core.py", *many}
    reached = ReachedBy(["conftest.py"], "call-graph", 1, ("conftest.py",))

    expanded = _expand_scopes(
        "src/core.py", reached, expand_test_scopes(reached.all_tests, test_files)
    )

    assert expanded.total == MAX_TESTS_PER_TARGET + 21
    assert len(expanded.tests) == MAX_TESTS_PER_TARGET
    assert expanded.tests[0] == "tests/unit/test_core.py"  # named for the target
    assert expanded.all_tests is None  # plan ranking scores the capped list only
    validation = build_validation_plan(_plan("fixture"), {}, {"src/core.py": expanded})
    assert validation.total == MAX_TESTS_PER_TARGET + 21
    assert validation.truncated is True
    assert validation.tests[0] == "tests/unit/test_core.py"


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
        # Both calls start from a cold test-map cache, so the count measures the
        # payload, not whether the first call warmed it.
        clear_test_map_cache()
        statements.clear()
        await hydrate_recommendations(async_session, repo.id, [_plan("one", file_path="src/f0.py")])
        small = len(statements)
        await save_test_coverage(async_session, repo.id, records, source_format="coverage.py")
        await async_session.commit()
        clear_test_map_cache()
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


def test_a_plan_in_a_language_with_no_known_runner_suggests_no_command() -> None:
    """The fallback used to be ``npm run test`` for every file that was not
    Python or JS, in repos with no ``package.json``. A wrong command is worse
    than none: every consumer treats an empty list as nothing to suggest."""
    for path in ("src/Foo/Bar.cs", "src/native/foo.cpp", "server/Translog.java", "a/b.go"):
        plan = build_validation_plan(_plan("target", file_path=path), {}, {})
        assert plan.commands == [], path


def test_tests_in_a_language_with_no_known_runner_suggest_no_command() -> None:
    reached = ReachedBy(
        via="call-graph",
        total=1,
        tests=["tests/BarTests.cs::Ok"],
        all_tests=["tests/BarTests.cs::Ok"],
    )
    plan = build_validation_plan(
        _plan("target", file_path="src/Foo/Bar.cs"), {}, {"src/Foo/Bar.cs": reached}
    )
    assert plan.tests == ["tests/BarTests.cs::Ok"]
    assert plan.commands == []


def test_a_plan_no_test_reaches_has_no_command_and_asks_for_a_characterization_test() -> None:
    """A bare ``pytest`` or ``npm test`` ran a whole suite as if it guarded the change."""
    for path in ("a/b.py", "web/app.ts"):
        plan = build_validation_plan(_plan("target", file_path=path), {}, {})
        assert plan.basis == "unknown"
        assert plan.commands == [], path
        assert plan.prerequisite == (
            "No test reaches this; add a characterization test for `target` before the edit."
        )
        assert plan.as_dict()["prerequisite"] == plan.prerequisite
    # A target that is not a symbol name is named by its file.
    split = build_validation_plan(_plan("core.py -> 3 files", rtype="split_file"), {}, {})
    assert "characterization test for `core.py` before" in split.prerequisite


def test_a_reached_plan_carries_no_prerequisite() -> None:
    reached = ReachedBy(["tests/test_b.py"], "call-graph", 1, ("tests/test_b.py",))
    plan = build_validation_plan(_plan("target", file_path="a/b.py"), {}, {"a/b.py": reached})
    assert plan.prerequisite is None
    assert plan.commands == ["pytest tests/test_b.py"]


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


# The walker shape from the audit: a file reached by many tests, most of which
# only pass through it. Alphabetical order used to lead with the bystanders.
_WALKER = "src/health/walker.py"
_WALK_FILE = f"{_WALKER}::walk_file"
_WALKER_TESTS = [
    "tests/health/conftest.py",
    "tests/health/test_assertions.py",
    "tests/health/test_bystander.py",
    "tests/health/test_imports_it.py",
    "tests/health/test_two_hops.py",
    "tests/health/test_walker.py",
    "tests/health/test_walks_a_lot.py",
]


def _walker_plan() -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type="extract_method",
        file_path=_WALKER,
        target_symbol="walk_file",
        line_start=94,
        line_end=208,
        plan={},
        evidence={},
        impact_delta=1.0,
        effort_bucket="M",
        blast_radius={},
        confidence="high",
        source_biomarker="long_function",
    )


def _walker_evidence() -> ValidationEvidence:
    return ValidationEvidence(
        symbols={_WALKER: [(_WALK_FILE, 94, 208), (f"{_WALKER}::helper", 210, 220)]},
        symbol_reach={
            _WALK_FILE: {
                "tests/health/conftest.py": ReachDistance(1, 3),
                "tests/health/test_assertions.py": ReachDistance(1, 1),
                "tests/health/test_walks_a_lot.py": ReachDistance(1, 9),
                "tests/health/test_two_hops.py": ReachDistance(2, 4),
            }
        },
        imports={_WALKER: {"tests/health/test_imports_it.py": frozenset({"walk_file"})}},
    )


def _walker_reached() -> dict[str, ReachedBy]:
    reach = {test: ReachDistance(1, 1) for test in _WALKER_TESTS}
    return {
        _WALKER: ReachedBy(
            _WALKER_TESTS, "call-graph", len(_WALKER_TESTS), tuple(_WALKER_TESTS), reach
        )
    }


def test_tests_that_call_the_changed_symbol_lead_the_list_with_reasons() -> None:
    validation = build_validation_plan(
        _walker_plan(), {}, _walker_reached(), evidence=_walker_evidence()
    )
    assert validation.tests == [
        # Named for the file: the test someone wrote for it leads.
        "tests/health/test_walker.py",
        # Calls walk_file directly, more of its functions first.
        "tests/health/test_walks_a_lot.py",
        "tests/health/test_assertions.py",
        # Imports walk_file by name, no call edge.
        "tests/health/test_imports_it.py",
        # Two calls away from walk_file.
        "tests/health/test_two_hops.py",
        # Reaches the file only.
        "tests/health/test_bystander.py",
        # Test support runs nothing on its own, however close it is.
        "tests/health/conftest.py",
    ]
    assert validation.reasons == {
        "tests/health/test_walks_a_lot.py": "calls walk_file from 9 test functions",
        "tests/health/test_assertions.py": "calls walk_file",
        "tests/health/test_imports_it.py": "imports walk_file",
        "tests/health/test_two_hops.py": "reaches walk_file in 2 calls",
        "tests/health/test_walker.py": "calls into walker.py",
        "tests/health/test_bystander.py": "calls into walker.py",
        "tests/health/conftest.py": "calls walk_file from 3 test functions",
    }
    assert validation.targets[0].tests == validation.tests
    assert validation.as_dict()["reasons"] == validation.reasons


def test_measured_coverage_of_the_changed_lines_outranks_every_graph_signal() -> None:
    measured = {
        _WALKER: [
            {"test_id": "tests/health/test_bystander.py::test_x", "covered_lines": [100, 150]},
            {"test_id": "tests/health/test_walker.py::test_y", "covered_lines": [99]},
        ]
    }
    validation = build_validation_plan(
        _walker_plan(), measured, _walker_reached(), evidence=_walker_evidence()
    )
    assert validation.tests == [
        "tests/health/test_bystander.py::test_x",
        "tests/health/test_walker.py::test_y",
    ]
    assert validation.reasons == {
        "tests/health/test_bystander.py::test_x": "covers lines 100-150",
        "tests/health/test_walker.py::test_y": "covers line 99",
    }


def test_the_cap_keeps_the_strongest_evidence_and_reasons_follow_it() -> None:
    validation = build_validation_plan(
        _walker_plan(), {}, _walker_reached(), evidence=_walker_evidence(), test_limit=2
    )
    assert validation.tests == [
        "tests/health/test_walker.py",
        "tests/health/test_walks_a_lot.py",
    ]
    assert list(validation.reasons) == validation.tests
    assert validation.total == len(_WALKER_TESTS)
    assert validation.truncated is True


def test_without_graph_evidence_name_then_directory_reasons_are_given() -> None:
    plan = _plan("Core", file_path="src/pkg/core.py")
    reached = ReachedBy(
        ["tests/other/test_misc.py", "tests/pkg/test_near.py", "tests/unit/test_core.py"],
        "name-match",
        3,
    )
    validation = build_validation_plan(plan, {}, {"src/pkg/core.py": reached})
    assert validation.tests == [
        "tests/unit/test_core.py",
        "tests/pkg/test_near.py",
        "tests/other/test_misc.py",
    ]
    assert validation.reasons == {
        "tests/unit/test_core.py": "named for core.py",
        "tests/pkg/test_near.py": "shares pkg",
        "tests/other/test_misc.py": "reaches core.py",
    }


def test_a_line_range_target_resolves_to_its_enclosing_symbol() -> None:
    plan = _walker_plan()
    plan.target_symbol = "walker.py:120-130"
    spans = [(f"{_WALKER}::__module__", 0, 0), (_WALK_FILE, 94, 208), (f"{_WALKER}::inner", 118, 140)]
    assert target_symbol_ids(plan, _WALKER, set(range(120, 131)), spans) == [f"{_WALKER}::inner"]
    # A full id, as a performance plan stores it, resolves as itself.
    plan.target_symbol = _WALK_FILE
    assert target_symbol_ids(plan, _WALKER, None, spans) == [_WALK_FILE]


def test_a_test_file_the_plan_edits_is_listed_as_edited() -> None:
    plan = _plan("Core", rtype="split_file", file_path="src/core.py")
    plan.line_start = plan.line_end = None
    plan.blast_radius = {"files": ["tests/test_user.py"]}
    inferred = {
        "src/core.py": ReachedBy(["tests/test_core.py"], "import-graph", 1),
        "tests/test_user.py": ReachedBy(["tests/test_user.py"], "import-graph", 1),
    }
    validation = build_validation_plan(plan, {}, inferred)
    # The test named for the file leads; the edited one follows.
    assert validation.tests == ["tests/test_core.py", "tests/test_user.py"]
    assert validation.reasons["tests/test_user.py"] == "edited by this plan"


def test_a_same_stem_test_leads_the_attention_plan() -> None:
    """``_health_items`` in ``services/attention.py``: the golden test written for
    the file stays first, ahead of tests that only share its directory."""
    path = "packages/server/src/repowise/server/services/attention.py"
    tests = [
        "tests/unit/server/test_alerts.py",
        "tests/unit/server/test_overview_attention.py",
        "tests/unit/server/test_attention_golden.py",
        "tests/unit/cli/test_far_away.py",
    ]
    reached = ReachedBy(tests, "call-graph", len(tests), tuple(tests))
    for order_tests in (True, False):
        plan = build_validation_plan(
            _plan("_health_items", rtype="extract_method", file_path=path),
            {},
            {path: reached},
            order_tests=order_tests,
        )
        assert plan.tests[0] == "tests/unit/server/test_attention_golden.py", order_tests
        assert plan.tests[-1] == "tests/unit/cli/test_far_away.py", order_tests


def test_a_qualified_spec_name_counts_as_same_stem() -> None:
    path = "src/agents/run/attempt.ts"
    tests = ["src/other/zz.test.ts", "src/agents/run/attempt.spawn-workspace.test.ts"]
    reached = ReachedBy(tests, "call-graph", 2, tuple(tests))
    plan = build_validation_plan(_plan("run", file_path=path), {}, {path: reached})
    assert plan.tests[0] == "src/agents/run/attempt.spawn-workspace.test.ts"
    assert plan.reasons[plan.tests[0]] == "named for attempt.ts"


def test_hubs_are_files_above_the_fan_in_bar_or_in_the_top_percent() -> None:
    fan_in = {f"src/m{i:03}.py": 2 for i in range(300)}
    fan_in |= {"src/top.py": 12, "src/wide.py": 51, "src/second.py": 11}
    fan_in |= {"tests/test_a.py": 90, "external:os": 900}
    # The top 1% of 303 files is three, and the floor keeps a fan-in of 2 out.
    assert hub_files(fan_in, {"tests/test_a.py"}) == {"src/top.py", "src/wide.py", "src/second.py"}
    small = {"src/a.py": 4, "src/b.py": 1}
    assert hub_files(small, set()) == frozenset()
