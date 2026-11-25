"""The slowest tests, and the groups of tests that make a suite slow."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.analysis.health.coverage.model import TestCoverage
from repowise.core.evidence.suite_cost import (
    Fixture,
    cost_fold,
    excess_clusters,
    suite_costs,
    suite_summary,
    timing_summary,
)
from repowise.core.persistence.crud import save_evidence_run
from repowise.core.persistence.crud.analysis.coverage_map import save_test_coverage
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GraphEdge, GraphNode
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
                   environment: str = "linux-ci", at: datetime = T0, partial: bool = False,
                   summary: str | None = None) -> None:
    run = {
        "run_key": f"{workload}-{environment}-{at.isoformat()}", "kind": "test_timing",
        "format": "junit", "source": "cli", "workload_name": workload, "workload_class": "unit_test",
        "environment": environment, "value_kind": "wall", "unit": "s", "commit_sha": HEAD,
        "ingested_at": at, "measured_at": at, "mapping_partial": partial,
        "timing_summary_json": summary,
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
    assert out["cost"] is None


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


async def test_a_partly_mapped_run_stands_in_for_a_workload_with_no_other(session) -> None:
    repo = await insert_repo(session)
    await _timings(session, repo.id, [_t(SLOW, 2.0)], partial=True)

    out = await suite_costs(session, repo.id, HEAD)

    assert out["status"] == "measured"
    assert out["runs"][0]["mapping_partial"] is True


async def test_a_newer_partial_run_elsewhere_never_displaces_a_complete_one(session) -> None:
    repo = await insert_repo(session)
    await _timings(session, repo.id, [_t(SLOW, 3.0)])
    await _timings(session, repo.id, [_t(SLOW, 9.0)], environment="macos-local",
                   at=T0 + timedelta(days=1), partial=True)

    out = await suite_costs(session, repo.id, HEAD)

    assert [(r["environment"], r["mapping_partial"]) for r in out["runs"]] == [("linux-ci", False)]
    assert out["slow_tests"][0]["duration_s"] == 3.0


def test_the_fold_covers_every_test_by_file_and_by_duration() -> None:
    rows = [
        _t("tests/a.py::one", 0.0005),
        _t("tests/a.py::two", 0.001),  # on a bound: that bound's bucket
        _t("tests/a.py::three", 2.5),
        _t("tests/b.py::four", 250.0),
        _t("tests/b.py::skipped", None),
        {"test_id": "Loose", "test_file": None, "duration_s": 0.05},
    ]

    fold = timing_summary(rows)

    assert (fold["tests"], fold["timed"], fold["seconds"]) == (6, 5, 252.5515)
    assert fold["histogram"] == [
        [2, 0.0015], [0, 0.0], [1, 0.05], [0, 0.0], [1, 2.5], [0, 0.0], [1, 250.0]
    ]
    assert fold["files"] == {"tests/a.py": [2.5015, 3], "tests/b.py": [250.0, 1]}


def test_the_suite_adds_up_each_runs_fold_or_says_nothing() -> None:
    first = timing_summary([_t("tests/a.py::one", 1.0), _t("tests/b.py::two", 3.0)])
    second = timing_summary([_t("tests/a.py::three", 4.0)])
    runs = [SimpleNamespace(timing_summary_json=json.dumps(f)) for f in (first, second)]

    suite = suite_summary(runs, limit=1)

    assert (suite["tests"], suite["timed"], suite["seconds"]) == (3, 3, 8.0)
    assert suite["test_files"] == [{"test_file": "tests/a.py", "seconds": 5.0, "tests": 2}]
    assert suite["test_files_total"] == 2
    assert suite["histogram"][3:5] == [
        {"le": 1.0, "tests": 1, "seconds": 1.0},
        {"le": 10.0, "tests": 2, "seconds": 7.0},
    ]
    assert suite["histogram"][-1]["le"] is None
    # A run stored before the fold would leave the totals short: none is given.
    assert suite_summary([*runs, SimpleNamespace(timing_summary_json=None)]) is None
    # A fold of another bucket set (or garbage) reads as absent, never a crash.
    other = json.dumps({**first, "histogram": first["histogram"][:3]})
    assert suite_summary([SimpleNamespace(timing_summary_json=other)]) is None
    assert suite_summary([SimpleNamespace(timing_summary_json="{")]) is None
    assert suite_summary([]) is None


async def test_the_suite_covers_tests_the_cap_left_out(session) -> None:
    repo = await insert_repo(session)
    every = [_t(SLOW, 3.0), _t(FAST, 0.1), _t(OTHER, 0.2)]
    stored = every[:1]  # the cap kept the slowest
    await _timings(session, repo.id, stored, summary=json.dumps(timing_summary(every)))

    out = await suite_costs(session, repo.id, HEAD)

    assert out["tests_total"] == 1
    assert out["suite"]["tests"] == 3
    assert out["suite"]["test_files"] == [
        {"test_file": "tests/test_app.py", "seconds": 3.3, "tests": 3}
    ]


async def test_adding_the_same_report_again_backfills_a_missing_fold(session) -> None:
    repo = await insert_repo(session)
    rows = [_t(SLOW, 3.0)]
    await _timings(session, repo.id, rows)  # stored before the fold existed
    assert (await suite_costs(session, repo.id, HEAD))["suite"] is None

    await _timings(session, repo.id, rows, summary=json.dumps(timing_summary(rows)))

    assert (await suite_costs(session, repo.id, HEAD))["suite"]["tests"] == 1


def _store_suite() -> tuple[dict, dict, dict]:
    """65 s over 20 tests: 5 store tests (10 s each, fixture ``indexed``) that also
    open the plugin registry, 5 more registry tests (2 s), 10 quick ones (0.5 s).
    Every test imports src/config.py."""
    store = [f"tests/test_store.py::test_{i}" for i in range(5)]
    plugins = [f"tests/test_plugins.py::test_{i}" for i in range(5)]
    quick = [f"tests/test_util.py::test_{i}" for i in range(10)]
    seconds = {**dict.fromkeys(store, 10.0), **dict.fromkeys(plugins, 2.0), **dict.fromkeys(quick, 0.5)}
    reach = {
        **{t: {"src/config.py", "src/store.py", "src/db.py", "src/plugins.py"} for t in store},
        **{t: {"src/config.py", "src/plugins.py"} for t in plugins},
        **{t: {"src/config.py", "src/util.py"} for t in quick},
    }
    indexed = Fixture("indexed", "tests/test_store.py", 48)
    tmp = Fixture("tmp_path", "_pytest/tmpdir.py", None)
    session = Fixture("session", "tests/conftest.py", 9)
    registered = Fixture("registered", "tests/conftest.py", 20)
    fixtures = {
        **{t: {indexed, tmp} for t in store},
        store[0]: {indexed, tmp, session},
        **{t: {registered} for t in plugins[:2]},
        **{t: {tmp} for t in quick[:7]},
        **{t: {tmp, registered} for t in quick[7:]},
    }
    return seconds, reach, fixtures


def test_a_group_inside_a_larger_one_is_named_first_with_the_fixture_only_it_uses() -> None:
    seconds, reach, fixtures = _store_suite()

    store, plugins = excess_clusters(seconds, reach, 65.0, fixtures)

    # The registry group (10 tests, 55 s over the rest) holds the store's 5 tests,
    # which carry 45 s of it: the store is the answer, the registry's rest is next.
    assert store["files"] == ["src/db.py", "src/store.py"]
    assert (store["tests"], store["mean_s"], store["others_mean_s"], store["excess_s"]) == (5, 10.0, 1.0, 45.0)
    assert store["share"] == round(45 / 65, 4)
    assert store["within"] == {"files": ["src/plugins.py"], "files_total": 1, "tests": 10}
    assert store["fixture"] == {
        "name": "indexed", "file_path": "tests/test_store.py", "line": 48, "tests": 5, "others": 0
    }
    assert [t["test_id"] for t in store["slowest"]] == [f"tests/test_store.py::test_{i}" for i in range(3)]
    # Once the store's tests leave the pool, the registry's own 5 tests are 7.5 s over the rest.
    assert (plugins["files"], plugins["tests"], plugins["excess_s"]) == (["src/plugins.py"], 5, 7.5)
    assert plugins["within"] is None
    # `registered`: 2 of its 5 tests and 2 other timed tests; `tmp_path` is everybody's.
    assert plugins["fixture"] is None


def test_a_file_nearly_every_test_runs_is_never_a_group() -> None:
    seconds, reach, fixtures = _store_suite()

    clusters = excess_clusters(seconds, reach, 65.0, fixtures)

    assert all("src/config.py" not in c["files"] for c in clusters)
    # With only the setup import in common, nothing groups.
    assert excess_clusters(seconds, {t: {"src/config.py"} for t in seconds}, 65.0) == []


def test_groups_stop_below_five_seconds_over_the_rest() -> None:
    seconds = {f"t{i}": 1.0 for i in range(70)} | {f"s{i}": 2.0 for i in range(4)}
    reach = {t: {"src/a.py"} for t in seconds} | {f"s{i}": {"src/a.py", "src/slow.py"} for i in range(4)}

    # 4 tests 1 s over the rest's pace: 4 s, under the bar.
    assert excess_clusters(seconds, reach, sum(seconds.values())) == []
    out = cost_fold(seconds, reach)
    assert (out["verdict"], out["clusters"]) == ("small_excess", [])


def test_a_suite_under_a_minute_lists_nothing_even_with_a_map() -> None:
    seconds, reach, fixtures = _store_suite()
    quick = {t: s / 2 for t, s in seconds.items()}  # 32.5 s

    out = cost_fold(quick, reach, fixtures)

    assert (out["verdict"], out["clusters"], out["seconds"], out["timed"]) == ("small_suite", [], 32.5, 20)


def test_without_a_per_test_map_only_the_concentration_is_given() -> None:
    seconds, _, _ = _store_suite()

    out = cost_fold(seconds, None)

    assert (out["verdict"], out["clusters"]) == ("no_test_map", [])
    assert out["command"] == "pytest --cov --cov-context=test"
    # The slowest tenth (2 of 20 tests) take 20 s of 65 s.
    assert out["concentration"] == {"tests": 2, "share": round(20 / 65, 4)}


def test_concentration_takes_the_suite_from_the_fold_when_the_cap_cut_rows() -> None:
    # The cap kept the 3 slowest of 40 tests; the fold says the suite is 40 tests, 100 s.
    out = cost_fold({"a": 30.0, "b": 20.0, "c": 10.0}, None, timed=40, suite_seconds=100.0)

    assert out["concentration"] == {"tests": 3, "share": 0.6}
    assert (out["timed"], out["seconds"]) == (40, 100.0)


async def test_clusters_come_from_the_per_test_map_and_the_indexed_fixture_edges(session) -> None:
    repo = await insert_repo(session)
    seconds, reach, _ = _store_suite()
    await _timings(session, repo.id, [_t(t, s, symbol_id=t.split("[")[0]) for t, s in seconds.items()])
    await save_test_coverage(
        session,
        repo.id,
        [TestCoverage(t, path, [1], test_file=t.split("::")[0]) for t, paths in reach.items() for path in paths]
        # Test helpers a store test runs: never production, never in a group.
        + [TestCoverage(f"tests/test_store.py::test_{i}", "tests/helpers.py", [1]) for i in range(5)],
        source_format="coverage.py",
    )
    fixture = "tests/test_store.py::indexed"
    session.add(GraphNode(repository_id=repo.id, node_id=fixture, node_type="symbol", kind="function",
                          name="indexed", file_path="tests/test_store.py", start_line=48))
    for i in range(5):
        session.add(GraphEdge(repository_id=repo.id, source_node_id=f"tests/test_store.py::test_{i}",
                              target_node_id=fixture, edge_type="framework_binds",
                              hint_source="pytest_fixture"))
    # Another kind of edge between the same symbols is not a fixture request.
    session.add(GraphEdge(repository_id=repo.id, source_node_id="tests/test_util.py::test_0",
                          target_node_id=fixture, edge_type="calls"))
    await session.flush()

    cost = (await suite_costs(session, repo.id, HEAD))["cost"]

    assert cost["verdict"] == "clusters"
    store = cost["clusters"][0]
    assert store["files"] == ["src/db.py", "src/store.py"]
    assert store["fixture"] == {
        "name": "indexed", "file_path": "tests/test_store.py", "line": 48, "tests": 5, "others": 0
    }


async def test_no_map_naming_a_timed_test_gives_the_concentration_only(session) -> None:
    repo = await insert_repo(session)
    await _timings(session, repo.id, [_t(SLOW, 50.0), _t(FAST, 20.0)])
    await save_test_coverage(
        session, repo.id, [TestCoverage("lcov-suite", "src/app.py", [1])], source_format="lcov"
    )

    cost = (await suite_costs(session, repo.id, HEAD))["cost"]

    assert (cost["verdict"], cost["clusters"]) == ("no_test_map", [])
    assert cost["concentration"] == {"tests": 1, "share": round(50 / 70, 4)}


def test_tests_the_row_cap_cut_count_among_the_others() -> None:
    seconds, reach, _ = _store_suite()
    stored = {t: s for t, s in seconds.items() if s >= 2.0}  # the cap cut the 10 quick tests

    (store, plugins) = cost_fold(stored, reach, timed=20, suite_seconds=65.0)["clusters"]

    # The same answer as with every row: the cut tests are the others' pace.
    assert (store["excess_s"], store["others_mean_s"]) == (45.0, 1.0)
    assert plugins["excess_s"] == 7.5


async def test_two_workloads_running_the_same_tests_count_each_test_once(session) -> None:
    repo = await insert_repo(session)
    rows = [_t(SLOW, 40.0), _t(FAST, 1.0)]
    for env in ("linux-ci", "windows-ci"):
        await _timings(session, repo.id, rows, workload=f"pytest {env}", environment=env,
                       summary=json.dumps(timing_summary(rows)))

    cost = (await suite_costs(session, repo.id, HEAD))["cost"]

    # The folds add up to 82 s over 4 tests; the suite is 41 s over 2.
    assert (cost["verdict"], cost["seconds"], cost["timed"]) == ("small_suite", 41.0, 2)


def test_a_file_nearly_every_remaining_test_runs_is_never_a_later_group() -> None:
    seconds = {f"a{i}": 10.0 for i in range(10)} | {f"b{i}": 1.0 for i in range(19)} | {"c": 0.1}
    reach = {f"a{i}": {"src/a.py"} for i in range(10)} | {f"b{i}": {"src/x.py"} for i in range(19)}
    reach["c"] = {"src/c.py"}

    clusters = excess_clusters(seconds, reach, sum(seconds.values()))

    # x.py: 19 of 30 tests at first, 19 of the 20 left once a.py's group leaves.
    assert [c["files"] for c in clusters] == [["src/a.py"]]


def test_a_row_cap_under_a_tenth_of_the_suite_names_only_the_stored_tests() -> None:
    out = cost_fold({"a": 30.0, "b": 20.0}, None, timed=100, suite_seconds=100.0)

    assert out["concentration"] == {"tests": 2, "share": 0.5}
