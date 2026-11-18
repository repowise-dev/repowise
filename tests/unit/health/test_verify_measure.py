"""A performance plan's ``verify.measure``: resolved from the checkout once, carried
on the validation profile, and absent from every other plan."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.analysis.health.fix_first._parts import _verify
from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.recipe import build_recipe, verify_check
from repowise.core.analysis.health.refactoring.recommendations import (
    ValidationEvidence,
    build_validation_plan,
)
from repowise.core.analysis.health.refactoring.serving import validation_from_profile
from repowise.core.analysis.test_reachability import clear_test_map_cache
from repowise.core.perf_measure import BEFORE_AFTER_FLOOR, verify_measure
from repowise.core.persistence.crud.analysis.refactoring_recommendations import (
    hydrate_recommendations,
)
from repowise.core.persistence.database import init_db
from tests.unit.persistence.helpers import insert_repo

_TARGET = {"workload": "pytest bench", "class": "benchmark", "command": "pytest --benchmark-only", "cwd": ""}


def _plan(rtype: str, file_path: str = "app/rows.py") -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type=rtype,
        file_path=file_path,
        target_symbol=f"{file_path}::load_rows",
        line_start=10,
        line_end=20,
        plan={},
        evidence={},
        impact_delta=0.0,
        effort_bucket="M",
        blast_radius={},
        confidence="high",
        source_biomarker="io_in_loop",
    )


def _validated(rtype: str) -> dict:
    facts = ValidationEvidence(timed={"app/rows.py": _TARGET})
    return build_validation_plan(_plan(rtype), {}, {}, evidence=facts).detail_dict()


def test_a_performance_plan_carries_the_measure_lines() -> None:
    measure = _validated("performance_fix")["measure"]

    assert measure == verify_measure(_TARGET, "rows.load_rows")
    name = "before-rows.load_rows"
    assert measure["before"] == f"repowise perf measure --save {name} -- {_TARGET['command']}"
    assert measure["after"] == f"repowise perf measure --against {name} -- {_TARGET['command']}"
    assert (measure["metric"], measure["baseline"]) == ("median wall time", name)
    assert measure["tolerance"] == BEFORE_AFTER_FLOOR
    assert "cwd" not in measure


def test_other_plans_and_unmatched_files_carry_no_measure_key() -> None:
    assert "measure" not in _validated("extract_method")
    other = build_validation_plan(
        _plan("performance_fix", "lib/other.py"), {}, {},
        evidence=ValidationEvidence(timed={"app/rows.py": _TARGET}),
    )
    assert "measure" not in other.detail_dict()


def test_lists_never_carry_the_measure_only_detail_does() -> None:
    plan = build_validation_plan(
        _plan("performance_fix"), {}, {}, evidence=ValidationEvidence(timed={"app/rows.py": _TARGET})
    )
    assert "measure" not in plan.as_dict()
    assert plan.detail_dict() == {**plan.as_dict(), "measure": plan.measure}


def test_verify_check_passes_measure_beside_commands_and_leaves_others_unchanged() -> None:
    with_measure = _validated("performance_fix")
    without = {k: v for k, v in with_measure.items() if k != "measure"}

    check = verify_check(with_measure)
    plain = verify_check(without)

    assert check["measure"] == with_measure["measure"]
    assert {k: v for k, v in check.items() if k != "measure"} == plain
    assert "measure" not in plain
    assert "measure" not in verify_check(None)


def test_a_stored_profile_keeps_its_measure() -> None:
    profile = _validated("performance_fix")

    assert validation_from_profile(profile).detail_dict()["measure"] == profile["measure"]
    assert _verify(profile).measure == profile["measure"]
    assert _verify({k: v for k, v in profile.items() if k != "measure"}).measure is None


def test_the_recipe_of_a_performance_plan_names_the_measure() -> None:
    detail = {"refactoring_type": "performance_fix", "file_path": "app/rows.py",
              "validation": _validated("performance_fix")}
    verify = build_recipe(detail)["postconditions"][0]
    assert verify["measure"]["baseline"] == "before-rows.load_rows"


async def test_hydration_with_a_checkout_times_the_units_benchmark(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\ndependencies = ["pytest-benchmark"]\n'
        '[project.scripts]\napp = "app.cli:main"\n'
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "cli.py").write_text("def main():\n    return 0\n")
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        repo = await insert_repo(session)
        await session.commit()
        clear_test_map_cache()
        rows = [_plan("performance_fix"), _plan("extract_method")]
        ranked = await hydrate_recommendations(session, repo.id, rows, checkout=str(tmp_path))
        by_type = {r.suggestion.refactoring_type: r for r in ranked}
        perf, other = by_type["performance_fix"], by_type["extract_method"]
        (live,) = await hydrate_recommendations(session, repo.id, rows[:1])
    await engine.dispose()

    measure = perf.validation.detail_dict()["measure"]
    assert perf.rank_facts()["validation"]["measure"] == measure
    assert perf.detail_dict()["validation"]["measure"] == measure
    assert "measure" not in perf.as_dict()["validation"]
    # The entry point waits for its arguments, so the benchmark is timed.
    assert (measure["workload"], measure["workload_class"]) == ("pytest bench", "benchmark")
    assert "--benchmark-json" not in measure["command"]
    assert "measure" not in other.validation.detail_dict()
    # Without a checkout there is nothing to read the unit from.
    assert "measure" not in live.validation.detail_dict()
