"""The slowest tests and test time per production file, measured or inferred."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.analysis.function_test_map import MappedTest, encode_tests
from repowise.core.analysis.health.coverage.model import TestCoverage
from repowise.core.evidence.suite_cost import suite_costs
from repowise.core.persistence.crud import save_evidence_run
from repowise.core.persistence.crud.analysis.coverage_map import save_test_coverage
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import FunctionFact, GraphNode
from tests.unit.persistence.helpers import insert_repo

HEAD = "a" * 40
T0 = datetime(2026, 11, 1, tzinfo=UTC)
SLOW = "tests/test_app.py::test_slow"
FAST = "tests/test_app.py::test_fast"
OTHER = "tests/test_app.py::test_other"


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    await init_db(engine)
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        yield s
    await engine.dispose()


async def _timings(session, repo_id: str, rows: list[dict], *, workload: str = "pytest",
                   environment: str = "linux-ci", at: datetime = T0, partial: bool = False) -> None:
    run = {
        "run_key": f"{workload}-{environment}-{at.isoformat()}", "kind": "test_timing",
        "format": "junit", "source": "cli", "workload_name": workload, "workload_class": "unit_test",
        "environment": environment, "value_kind": "wall", "unit": "s", "commit_sha": HEAD,
        "ingested_at": at, "measured_at": at, "mapping_partial": partial,
    }
    await save_evidence_run(session, repo_id, run, rows, history_runs=5, max_runs=20)


def _t(test_id: str, seconds: float | None, **extra) -> dict:
    return {"test_id": test_id, "test_file": test_id.split("::")[0], "duration_s": seconds,
            "outcome": "passed", **extra}


async def test_nothing_stored_says_what_writes_timings(session) -> None:
    repo = await insert_repo(session)

    out = await suite_costs(session, repo.id, HEAD)

    assert out["status"] == "no_test_timings"
    assert out["command"] == "repowise evidence setup"
    assert out["file_costs"] is None


async def test_slowest_first_from_the_newest_complete_run_of_each_workload(session) -> None:
    repo = await insert_repo(session)
    await _timings(session, repo.id, [_t(SLOW, 9.0)], at=T0 - timedelta(days=1))
    await _timings(session, repo.id, [_t(SLOW, 3.0), _t(FAST, 0.1), _t("tests/test_app.py::test_skip", None)])
    # A partial run is never a basis; another environment of the same workload is not added.
    await _timings(session, repo.id, [_t(FAST, 50.0)], at=T0 + timedelta(days=1), partial=True)
    await _timings(session, repo.id, [_t(FAST, 70.0)], environment="windows-local", at=T0 - timedelta(days=2))
    await _timings(session, repo.id, [_t("web/a.test.ts::adds", 1.0)], workload="vitest", at=T0 + timedelta(hours=1))

    out = await suite_costs(session, repo.id, HEAD, limit=2)

    assert out["status"] == "measured"
    assert [r["workload"] for r in out["runs"]] == ["vitest", "pytest"]
    assert all(r["freshness"] == "current" for r in out["runs"])
    assert [(t["test_id"], t["duration_s"]) for t in out["slow_tests"]] == [(SLOW, 3.0), ("web/a.test.ts::adds", 1.0)]
    assert out["tests_total"] == 3


async def test_file_cost_is_measured_from_the_per_test_map(session) -> None:
    repo = await insert_repo(session)
    await _timings(session, repo.id, [_t(SLOW, 3.0), _t(FAST, 0.5), _t(OTHER, 1.0)])
    await save_test_coverage(
        session,
        repo.id,
        [
            TestCoverage(SLOW, "src/app.py", [1, 2], test_file="tests/test_app.py"),
            TestCoverage(FAST, "src/app.py", [3], test_file="tests/test_app.py"),
            TestCoverage(FAST, "src/util.py", [1], test_file="tests/test_app.py"),
            TestCoverage(OTHER, "src/util.py", [3], test_file="tests/test_app.py"),
            TestCoverage("tests/test_app.py::test_untimed", "src/util.py", [2]),
            # A test helper run by a timed test: counted, never listed.
            TestCoverage(SLOW, "tests/conftest.py", [1], test_file="tests/test_app.py"),
        ],
        source_format="coverage.py",
    )

    costs = (await suite_costs(session, repo.id, HEAD))["file_costs"]

    assert costs["basis"] == "measured"
    assert costs["files"] == [
        {"file_path": "src/app.py", "test_seconds": 3.5, "timed_tests": 2, "tests": 2},
        {"file_path": "src/util.py", "test_seconds": 1.5, "timed_tests": 2, "tests": 3},
    ]
    assert costs["not_production"] == 1 and costs["every_test"] == 0


async def test_a_file_every_timed_test_runs_is_counted_not_listed(session) -> None:
    # Test setup imports src/config.py: every timed test runs it, so its sum is the whole
    # suite's time, which would lead the list and say nothing of the file.
    repo = await insert_repo(session)
    await _timings(session, repo.id, [_t(SLOW, 3.0), _t(FAST, 0.5), _t(OTHER, 1.0)])
    await save_test_coverage(
        session,
        repo.id,
        [
            TestCoverage(SLOW, "src/config.py", [1], test_file="tests/test_app.py"),
            TestCoverage(FAST, "src/config.py", [1], test_file="tests/test_app.py"),
            TestCoverage(SLOW, "src/app.py", [1], test_file="tests/test_app.py"),
            TestCoverage(FAST, "src/util.py", [1], test_file="tests/test_app.py"),
            # A timed test that runs only a test helper does not keep config.py listed.
            TestCoverage(OTHER, "tests/conftest.py", [1], test_file="tests/test_app.py"),
        ],
        source_format="coverage.py",
    )

    costs = (await suite_costs(session, repo.id, HEAD))["file_costs"]

    assert [f["file_path"] for f in costs["files"]] == ["src/app.py", "src/util.py"]
    assert costs["files_total"] == 2 and costs["every_test"] == 1


async def test_file_cost_is_inferred_from_nearest_tests_without_the_map(session) -> None:
    repo = await insert_repo(session)
    await _timings(session, repo.id, [
        _t(f"{SLOW}[a]", 2.0, symbol_id=SLOW), _t(f"{SLOW}[b]", 1.0, symbol_id=SLOW), _t(FAST, 0.5, symbol_id=FAST),
    ])
    for path in ("src/app.py", "tests/test_app.py"):
        session.add(GraphNode(repository_id=repo.id, node_id=path, node_type="file", file_path=path))
    for symbol, tests in (("src/app.py::run", [SLOW, FAST]), ("src/app.py::other", [FAST]),
                          ("tests/test_app.py::helper", [SLOW])):
        session.add(GraphNode(repository_id=repo.id, node_id=symbol, node_type="function",
                              file_path=symbol.split("::")[0]))
        session.add(FunctionFact(repository_id=repo.id, symbol_id=symbol,
                                 tests_json=encode_tests(MappedTest(t, 1) for t in tests)))
    await session.flush()

    costs = (await suite_costs(session, repo.id, HEAD))["file_costs"]

    # Both parametrizations of a test add up; a test reaching two functions of a file counts once.
    # The test helper is run by a timed test: counted, never listed.
    assert costs["basis"] == "inferred"
    # Every timed test reaches src/app.py, but it is the only file: listed, not hidden.
    assert costs["files"] == [{"file_path": "src/app.py", "test_seconds": 3.5, "timed_tests": 2, "tests": 2}]
    assert costs["not_production"] == 1 and costs["every_test"] == 0


async def test_a_partly_mapped_run_stands_in_for_a_workload_with_no_other(session) -> None:
    repo = await insert_repo(session)
    await _timings(session, repo.id, [_t(SLOW, 2.0)], partial=True)

    out = await suite_costs(session, repo.id, HEAD)

    assert out["status"] == "measured"
    assert out["runs"][0]["mapping_partial"] is True


async def test_a_map_naming_no_timed_test_falls_back_to_inferred(session) -> None:
    repo = await insert_repo(session)
    await _timings(session, repo.id, [_t(SLOW, 3.0)])
    await save_test_coverage(
        session, repo.id, [TestCoverage("lcov-suite", "src/app.py", [1])], source_format="lcov"
    )

    costs = (await suite_costs(session, repo.id, HEAD))["file_costs"]

    assert costs["basis"] == "inferred"


async def test_a_newer_partial_run_elsewhere_never_displaces_a_complete_one(session) -> None:
    repo = await insert_repo(session)
    await _timings(session, repo.id, [_t(SLOW, 3.0)])
    await _timings(session, repo.id, [_t(SLOW, 9.0)], environment="macos-local",
                   at=T0 + timedelta(days=1), partial=True)

    out = await suite_costs(session, repo.id, HEAD)

    assert [(r["environment"], r["mapping_partial"]) for r in out["runs"]] == [("linux-ci", False)]
    assert out["slow_tests"][0]["duration_s"] == 3.0
