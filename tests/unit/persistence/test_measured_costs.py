"""``crud.measured_costs``: what each workload's newest run measured, beside the static causes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert

from repowise.core.analysis.function_test_map import MappedTest, encode_tests
from repowise.core.persistence.crud import (
    measured_costs,
    measured_status,
    replace_symbol_hotness,
    replace_workload_hotness,
    unranked_reason,
)
from repowise.core.persistence.crud import tests_running_spans as spans_run
from repowise.core.persistence.models import (
    EvidenceBenchmarkResult,
    EvidenceRouteLatency,
    EvidenceRun,
    EvidenceStatementStats,
    EvidenceSymbolCost,
    FunctionFact,
    GitCommit,
    GitFunctionBlame,
    GitMetadata,
    GraphEdge,
    GraphNode,
    HealthFinding,
    PerformanceOpportunity,
    TestCoverageSource,
    TestCoverageTest,
)
from tests.unit.persistence.helpers import insert_repo, make_wiki_symbol

APP = "src/app.py"
NOW = datetime(2026, 11, 18, tzinfo=UTC)


def _run(repo_id: str, run_id: str, *, cls: str = "entry_point", day: int = 15) -> EvidenceRun:
    return EvidenceRun(
        id=run_id, repository_id=repo_id, run_key=run_id, kind="profile",
        format="repowise-evidence-v1", source="cli", workload_name=f"w-{run_id}",
        workload_class=cls, value_kind="wall", unit="samples", commit_sha="c" * 40,
        measured_at=datetime(2026, 11, day, tzinfo=UTC), total_value=1000.0,
        explained_value=950.0, repo_self_value=470.0, wait_value=300.0, library_value=120.0,
        unlisted_symbols=4,
    )


def _hot(name: str, run: str = "r1", rank: int = 1, **over) -> dict:
    return {
        "run_id": run,
        "symbol_id": f"{APP}::{name}",
        "rank": rank,
        "tier": "ranked",
        "total_share": 0.3,
        "self_share": 0.1,
        "share_low": 0.08,
        "share_high": 0.12,
        "samples": 100,
        "freshness": "current",
        "ordering_only": False,
        **over,
    }


async def _seed_code(session, repo_id: str) -> None:
    spans = {
        "serve": (1, 20),
        "inner": (5, 8),
        "load": (30, 40),
        "helper": (50, 60),
        "thin": (70, 80),
    }
    session.add_all(
        make_wiki_symbol(repo_id, APP, name, start_line=lo, end_line=hi)
        for name, (lo, hi) in spans.items()
    )
    serve = f"{APP}::serve"
    callers = ("src/main.py::main", "tests/test_app.py::test_serve")
    session.add_all(
        [
            GraphNode(repository_id=repo_id, node_id=serve, node_type="symbol"),
            *(GraphNode(repository_id=repo_id, node_id=c, node_type="symbol") for c in callers),
            *(
                GraphEdge(
                    repository_id=repo_id, source_node_id=c, target_node_id=serve,
                    edge_type="calls",
                )
                for c in callers
            ),
            # The file's owner: never shown on a function row.
            GitMetadata(
                repository_id=repo_id,
                file_path=APP,
                primary_owner_name="Ada",
                primary_owner_commit_pct=0.8,
            ),
            # ``serve``'s own lines: Grace wrote most, newest commit is "f" * 40.
            GitFunctionBlame(
                repository_id=repo_id, symbol_id=serve, file_path=APP, function_name="serve",
                start_line=1, end_line=20, mod_count=2, owner_name="Grace",
                owner_line_pct=0.75, commit_shas_json=json.dumps(["f" * 40, "e" * 40]),
            ),
            GitCommit(
                repository_id=repo_id, sha="f" * 40, subject="perf: serve faster (#42)",
                committed_at=datetime(2026, 10, 3, 12, tzinfo=UTC),
            ),
        ]
    )
    await session.execute(
        insert(FunctionFact.__table__),
        [
            {
                "repository_id": repo_id,
                "symbol_id": serve,
                "execution_role": "request",
                "tests_json": encode_tests([MappedTest("tests/test_app.py::test_serve", 1)]),
            }
        ],
    )
    await session.flush()


def _finding(repo_id: str, line: int, opportunity_id: str | None) -> HealthFinding:
    return HealthFinding(
        repository_id=repo_id,
        file_path=APP,
        biomarker_type="io_in_loop",
        severity="medium",
        line_start=line,
        line_end=line,
        dimension="performance",
        status="open",
        details_json="{}",
        opportunity_id=opportunity_id,
    )


def _cause(repo_id: str, opportunity_id: str, *, context: str = "production",
           symbol: str | None = None) -> PerformanceOpportunity:
    return PerformanceOpportunity(
        repository_id=repo_id, opportunity_id=opportunity_id, status="open",
        execution_context=context, intervention_symbol=symbol, file_path=APP,
        details_json=json.dumps({}),
    )


async def test_no_evidence_means_no_view(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    assert await measured_costs(async_session, repo.id, now=NOW) is None


async def test_each_workload_keeps_its_rows_with_context_and_static_links(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add_all(
        [
            _run(repo.id, "r1"),
            _run(repo.id, "r2", cls="load_test", day=16),
            _run(repo.id, "r3", cls="unit_test"),
            # On ``load``, with production evidence: linked on its row.
            _finding(repo.id, 35, "perf3_load"),
            _cause(repo.id, "perf3_load"),
            # Inside ``inner``, nested in ``serve``: not on ``serve``.
            _finding(repo.id, 6, "perf3_inner"),
            _cause(repo.id, "perf3_inner"),
            # On ``serve``, but its evidence is test code only: no link.
            _cause(repo.id, "perf3_tests", context="test", symbol=f"{APP}::serve"),
            # A cause whose edit site is ``helper``.
            _cause(repo.id, "perf3_helper", symbol=f"{APP}::helper"),
        ]
    )
    await async_session.flush()
    await replace_workload_hotness(
        async_session,
        repo.id,
        [
            _hot("serve"),
            _hot("load", rank=2, tier="lead_candidate"),
            # The load test ranks ``serve`` too: listed under both workloads.
            _hot("helper", run="r2"),
            _hot("serve", run="r2", rank=2),
            _hot("thin", run="r3"),
        ],
    )

    view = await measured_costs(async_session, repo.id, now=NOW)

    assert set(view) == {"measured", "measured_in_tests"}
    measured = view["measured"]
    # Newest run first; ranks run 1, 2 within each run.
    assert [(r["run_id"], r["rank"], r["symbol_id"]) for r in measured["rows"]] == [
        ("r2", 1, f"{APP}::helper"), ("r2", 2, f"{APP}::serve"),
        ("r1", 1, f"{APP}::serve"), ("r1", 2, f"{APP}::load"),
    ]
    assert measured["total"] == 4
    serve = measured["rows"][2]
    assert serve["lines"] == [1, 20]
    # The test caller is neither listed nor counted.
    assert serve["callers_total"] == 1 and serve["callers"] == ["src/main.py::main"]
    # Owner and last change from the function's own lines, not the file's owner.
    assert serve["owner"] == {"name": "Grace", "line_pct": 0.75}
    assert serve["last_change"] == {
        "sha": "f" * 40, "date": "2026-10-03", "subject": "perf: serve faster (#42)", "pr": 42,
    }
    # ``load`` has no blame row of its own: no owner line, never the file's owner.
    load = measured["rows"][3]
    assert load["owner"] is None and load["last_change"] is None
    assert serve["tests"] == ["tests/test_app.py::test_serve"]
    # No per-test map: the nearest test by call graph, labelled so.
    # No benchmark measures it: the one verify command reruns its workload.
    assert (serve["tests_basis"], serve["verify_basis"]) == ("call_graph", "workload")
    assert serve["verify"] == (
        "repowise perf measure --save before -- w-r1 before the change, "
        "then --against before after it"
    )
    # ``share`` is the cost (self), with its interval; inclusive is context.
    assert (serve["share"], serve["share_low"], serve["share_high"]) == (0.1, 0.08, 0.12)
    assert serve["inclusive"] == 0.3
    assert "on_static_findings" not in serve
    assert measured["rows"][3]["on_static_findings"] == ["perf3_load"]
    assert measured["rows"][0]["on_static_findings"] == ["perf3_helper"]
    assert measured["runs"][1] == {
        "run_id": "r1",
        "workload_class": "entry_point",
        "workload_name": "w-r1",
        "commit": "c" * 40,
        "measured_at": datetime(2026, 11, 15, tzinfo=UTC).isoformat(),
        "age_days": 3,
        "samples": 1000,
        "repo_self_share": 0.47,
        "wait_share": 0.3,
        "library_share": 0.12,
        # 1000 total less 470 + 300 + 120 in the three buckets.
        "unattributed_share": 0.11,
        "outside_production": 4,
        "value_kind": "wall",
        "ordering_only": False,
        # The workload's newest run.
        "newer_run": None,
    }
    assert [r["run_id"] for r in measured["runs"]] == ["r2", "r1"]

    tests_group = view["measured_in_tests"]
    assert [r["symbol_id"] for r in tests_group["rows"]] == [f"{APP}::thin"]
    assert [r["workload_class"] for r in tests_group["runs"]] == ["unit_test"]
    # Never mapped to tests: unknown, not "no tests".
    assert tests_group["rows"][0]["tests"] is None
    assert tests_group["rows"][0]["tests_basis"] is None


def _map(repo_id: str, tests: list[tuple[str, str]], ranges: list[str]) -> list:
    """A per-test map row for ``APP``: test *i* ran ``ranges[i]``."""
    return [
        *(
            TestCoverageTest(repository_id=repo_id, test_no=no, test_id=test_id, test_file=path)
            for no, (test_id, path) in enumerate(tests)
        ),
        TestCoverageSource(
            repository_id=repo_id,
            source_file=APP,
            tests_json=json.dumps([[no, r] for no, r in enumerate(ranges)]),
            test_count=len(tests),
            source_format="coverage.py",
        ),
    ]


async def test_the_per_test_map_names_the_tests_that_ran_each_function(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add_all(
        [
            _run(repo.id, "r1"),
            *_map(
                repo.id,
                [
                    ("tests/test_app.py::test_run[a]|run", "tests/test_app.py"),
                    ("tests/test_load.py::test_load", "tests/test_load.py"),
                    # A pytest-benchmark test: its stored result says so.
                    ("tests/test_app.py::test_timed", "tests/test_app.py"),
                    ("tests/test_app.py::test_module_only", "tests/test_app.py"),
                    # Ran only ``load``'s def line: an import, not a call.
                    ("tests/test_app.py::test_import_only", "tests/test_app.py"),
                ],
                ["3-6", "32-33,36", "2", "25", "30"],
            ),
            EvidenceBenchmarkResult(
                run_id="r1", benchmark_key="test_timed", repository_id=repo.id,
                symbol_id="tests/test_app.py::test_timed", median=1.0,
            ),
        ]
    )
    await async_session.flush()
    await replace_workload_hotness(
        async_session,
        repo.id,
        [_hot("serve"), _hot("load", rank=2), _hot("helper", rank=3)],
    )

    rows = {
        r["symbol_id"].partition("::")[2]: r
        for r in (await measured_costs(async_session, repo.id, now=NOW))["measured"]["rows"]
    }

    # The map has the file: only tests that ran the lines, never the static
    # nearest one the map did not see run it, nor the benchmark.
    assert (rows["serve"]["tests"], rows["serve"]["tests_basis"]) == (
        ["tests/test_app.py::test_run"],
        "per_test_map",
    )
    # A benchmark that measures the function beats its workload's rerun.
    assert rows["serve"]["verify"] == "pytest tests/test_app.py::test_timed --benchmark-only"
    assert rows["serve"]["verify_basis"] == "benchmark"
    assert rows["load"]["tests"] == ["tests/test_load.py::test_load"]
    assert rows["load"]["verify_basis"] == "workload"
    # The map has the file and no test ran it: none ran it, not unknown.
    assert (rows["helper"]["tests"], rows["helper"]["tests_basis"]) == ([], "per_test_map")


async def test_a_benchmark_calling_the_function_is_its_verify_command(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    bench = "app_test.go::BenchmarkServe"
    async_session.add_all(
        [
            _run(repo.id, "r1"),
            GraphNode(repository_id=repo.id, node_id=bench, node_type="symbol"),
            GraphEdge(
                repository_id=repo.id, source_node_id=bench, target_node_id=f"{APP}::serve",
                edge_type="calls",
            ),
        ]
    )
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("serve")])

    row = (await measured_costs(async_session, repo.id, now=NOW))["measured"]["rows"][0]

    assert row["verify"] == "go test -run '^$' -bench '^BenchmarkServe$' -count 5 ."
    assert row["tests"] == ["tests/test_app.py::test_serve"]


async def test_unit_test_evidence_never_lists_under_measured(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_run(repo.id, "r3", cls="unit_test"))
    await async_session.flush()
    await replace_workload_hotness(
        async_session, repo.id, [_hot("thin", run="r3", tier="lead_candidate")]
    )
    view = await measured_costs(async_session, repo.id, now=NOW)
    assert set(view) == {"measured_in_tests"}


async def test_a_class_that_may_not_lead_and_is_not_a_test_is_not_listed(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add_all([_run(repo.id, "r1"), _run(repo.id, "r9", cls="smoke")])
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("serve"), _hot("load", run="r9")])
    view = await measured_costs(async_session, repo.id, now=NOW)
    assert set(view) == {"measured"}
    assert [r["symbol_id"] for r in view["measured"]["rows"]] == [f"{APP}::serve"]


async def test_a_finding_with_no_cause_id_links_nothing(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add_all([_run(repo.id, "r1"), _finding(repo.id, 10, None)])
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("serve")])
    view = await measured_costs(async_session, repo.id, now=NOW)
    assert "on_static_findings" not in view["measured"]["rows"][0]


async def test_the_limit_applies_per_run_so_no_workload_is_cut_away(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add_all([_run(repo.id, "r1"), _run(repo.id, "r2", cls="load_test", day=16)])
    await async_session.flush()
    await replace_workload_hotness(
        async_session,
        repo.id,
        [_hot("serve"), _hot("load", rank=2), _hot("helper", rank=3), _hot("thin", run="r2")],
    )
    view = await measured_costs(async_session, repo.id, limit=1, now=NOW)
    assert [(r["run_id"], r["rank"]) for r in view["measured"]["rows"]] == [("r2", 1), ("r1", 1)]
    # The total counts the group before the cut.
    assert view["measured"]["total"] == 4


async def test_limit_zero_keeps_the_runs_and_totals_with_no_rows(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("serve"), _hot("load", rank=2)])
    view = await measured_costs(async_session, repo.id, limit=0, now=NOW)
    assert (view["measured"]["rows"], view["measured"]["total"]) == ([], 2)
    assert [r["run_id"] for r in view["measured"]["runs"]] == ["r1"]


async def test_a_tracer_run_says_so_even_with_no_rows_shown(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    await replace_workload_hotness(
        async_session, repo.id, [_hot("serve", ordering_only=True, self_share=None)]
    )
    view = await measured_costs(async_session, repo.id, limit=0, now=NOW)
    assert view["measured"]["runs"][0]["ordering_only"] is True


async def test_callers_are_read_in_one_statement_and_tests_never_count(async_session) -> None:
    from sqlalchemy import event

    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    load = f"{APP}::load"
    callers = [f"src/c{i:03}.py::f" for i in range(40)] + [f"tests/test_{i}.py::t" for i in range(60)]
    async_session.add_all(
        [GraphNode(repository_id=repo.id, node_id=load, node_type="symbol")]
        + [
            GraphEdge(repository_id=repo.id, source_node_id=c, target_node_id=load,
                      edge_type="calls", confidence=1.0 if c.startswith("src/c00") else 0.5)
            for c in callers
        ]
    )
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("serve"), _hot("load", rank=2)])

    engine = async_session.bind.sync_engine
    edge_reads: list[str] = []

    def _count(conn, cursor, statement, params, context, executemany):
        if "FROM graph_edges" in statement and "source_node_id" in statement:
            edge_reads.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        view = await measured_costs(async_session, repo.id, now=NOW)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    rows = {r["symbol_id"]: r for r in view["measured"]["rows"]}
    assert rows[load]["callers_total"] == 40
    # The most confident first, then by id.
    assert rows[load]["callers"] == [f"src/c00{i}.py::f" for i in range(3)]
    assert rows[f"{APP}::serve"]["callers_total"] == 1
    assert len([s for s in edge_reads if "ORDER BY" in s]) == 1


async def test_callers_are_production_ones_and_the_measured_caller_leads(async_session) -> None:
    """Tooling and test callers never count; the caller the run measured driving the
    function leads the list with its share, ahead of a more confident static one."""
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    load = f"{APP}::load"
    confidence = {
        "src/c1.py::a": 1.0,
        "src/c2.py::b": 0.9,
        "src/c3.py::c": 0.8,
        "src/c4.py::d": 0.7,
        "scripts/release.py::cut": 1.0,
        "tests/test_load.py::test_load": 1.0,
    }
    async_session.add_all(
        [GraphNode(repository_id=repo.id, node_id=load, node_type="symbol")]
        + [
            GraphEdge(repository_id=repo.id, source_node_id=c, target_node_id=load,
                      edge_type="calls", confidence=conf)
            for c, conf in confidence.items()
        ]
    )
    drivers = [
        {"name": "d", "share": 0.6, "symbol": "src/c4.py::d"},
        {"name": "cut", "share": 0.3, "symbol": "scripts/release.py::cut"},
        {"name": "main", "share": 0.1, "path": "src/main.py"},
    ]
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    # The run's own measured callers of ``load``.
    async_session.add(
        EvidenceSymbolCost(
            run_id="r1", symbol_id=load, repository_id=repo.id, drivers_json=json.dumps(drivers)
        )
    )
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("load")])

    (row,) = (await measured_costs(async_session, repo.id, now=NOW))["measured"]["rows"]

    assert row["callers_total"] == 4
    assert row["callers"] == ["src/c4.py::d", "src/c1.py::a", "src/c2.py::b"]
    assert row["caller_shares"] == {"src/c4.py::d": 0.6}


async def test_finalize_marks_a_cause_a_leading_workload_measured(async_session) -> None:
    from sqlalchemy import select

    from repowise.core.persistence.crud import finalize_performance_opportunities

    repo = await insert_repo(async_session)

    def owned(line: int, owner: str) -> HealthFinding:
        row = _finding(repo.id, line, None)
        row.file_path = "src/startup.py"
        row.function_name = owner
        row.details_json = json.dumps(
            {"boundary_kind": "db", "loop_magnitude": "grows_with_data",
             "role_owner": f"src/startup.py::{owner}", "execution_role": "startup"}
        )
        return row

    async_session.add_all(
        [
            _run(repo.id, "r1"),
            _run(repo.id, "r3", cls="unit_test"),
            owned(5, "warm_cache"),
            owned(25, "load_settings"),
            owned(45, "seed_defaults"),
        ]
    )
    await async_session.flush()
    await replace_workload_hotness(
        async_session,
        repo.id,
        [
            {**_hot("x"), "symbol_id": "src/startup.py::warm_cache", "tier": "lead_candidate"},
            # A unit-test run marks apart, and never lifts a cold role.
            {
                **_hot("x", run="r3"),
                "symbol_id": "src/startup.py::load_settings",
                "tier": "lead_candidate",
            },
            # A tracer's order is not a measured cost.
            {**_hot("y", rank=2, ordering_only=True), "symbol_id": "src/startup.py::seed_defaults"},
        ],
    )

    await finalize_performance_opportunities(async_session, repo.id)

    rows = (await async_session.execute(select(PerformanceOpportunity))).scalars().all()
    by_owner = {r.intervention_symbol.rpartition(":")[2]: r for r in rows}
    measured = json.loads(by_owner["warm_cache"].details_json)["facets"]
    assert measured["measured"] == "w-r1"
    # Above the lead bar in a leading workload: the cause inside it is confirmed.
    assert measured["confirmed"] == "measured"
    assert by_owner["warm_cache"].queue_eligible
    in_tests = json.loads(by_owner["load_settings"].details_json)["facets"]
    assert "measured" not in in_tests
    assert (in_tests["measured_in_tests"], in_tests["confirmed"]) == ("w-r3", "measured_in_tests")
    assert not by_owner["load_settings"].queue_eligible
    assert "measured" not in json.loads(by_owner["seed_defaults"].details_json)["facets"]


async def _two_startup_causes(
    session, repo_id: str, *, evidence: bool, unit_tests_only: bool = False
) -> None:
    """Two production causes; with evidence, a leading workload measured the
    one the static order puts second."""
    rows = []
    # The static order puts ``aaa_first`` ahead: its loop is shown to grow.
    for line, owner, size in ((5, "aaa_first", "grows_with_data"), (25, "zzz_measured", "unknown")):
        row = _finding(repo_id, line, None)
        row.file_path = "src/svc.py"
        row.function_name = owner
        row.details_json = json.dumps(
            {"boundary_kind": "db", "loop_magnitude": size,
             "role_owner": f"src/svc.py::{owner}", "execution_role": "request"}
        )
        rows.append(row)
    session.add_all(rows)
    if evidence:
        session.add_all([_run(repo_id, "r1"), _run(repo_id, "r3", cls="unit_test")])
    await session.flush()
    if evidence:
        lead = "r3" if unit_tests_only else "r1"
        await replace_workload_hotness(
            session,
            repo_id,
            [
                {**_hot("x", run=lead), "symbol_id": "src/svc.py::zzz_measured",
                 "tier": "lead_candidate"},
                # A unit-test run is no workload a cause was "not exercised in".
                {**_hot("y", run="r3"), "symbol_id": "src/svc.py::other"},
            ],
        )


async def _queue_and_rows(session, repo_id: str) -> tuple[list[str], list[dict], dict]:
    from sqlalchemy import select

    from repowise.core.analysis.health.perf.serving import parse_query
    from repowise.core.persistence.crud import finalize_performance_opportunities
    from repowise.core.persistence.models import PerformanceSummary
    from repowise.server.services.performance_health import PerformanceHealthService

    await finalize_performance_opportunities(session, repo_id)
    stored = (
        await session.execute(
            select(PerformanceOpportunity).order_by(PerformanceOpportunity.rank_position)
        )
    ).scalars().all()
    order = [r.intervention_symbol.rpartition(":")[2] for r in stored]
    query, _ = parse_query(context="all", actionability="all", proof="all", lift_defaults=True)
    service = PerformanceHealthService(session, repo_id, "repo")
    rest = (await service.page(query, row_decisions=True, evidence_per_item=8)).items
    agent = (await service.page(query)).items
    assert all("runtime_evidence" not in item for item in agent)
    summary = json.loads((await session.get(PerformanceSummary, repo_id)).summary_json)
    return order, rest, summary


async def test_evidence_puts_a_measured_cause_first_and_labels_the_rest(async_session) -> None:
    repo = await insert_repo(async_session)
    await _two_startup_causes(async_session, repo.id, evidence=True)
    order, rest, summary = await _queue_and_rows(async_session, repo.id)
    assert order == ["zzz_measured", "aaa_first"]
    assert summary["evidence_workloads"] == ["w-r1"]
    marks = {i["intervention_symbol"].rpartition(":")[2]: i["runtime_evidence"] for i in rest}
    assert marks["zzz_measured"] == {"state": "confirmed", "basis": "measured"}
    # Never hidden: the unmeasured cause is listed, in its static place among the rest.
    assert marks["aaa_first"] == {"state": "not_exercised", "workloads": ["w-r1"]}


async def test_without_evidence_the_order_and_rows_are_the_static_ones(async_session) -> None:
    repo = await insert_repo(async_session)
    await _two_startup_causes(async_session, repo.id, evidence=False)
    order, rest, summary = await _queue_and_rows(async_session, repo.id)
    assert order == ["aaa_first", "zzz_measured"]
    assert "evidence_workloads" not in summary
    assert all("runtime_evidence" not in item for item in rest)


async def test_unit_test_evidence_alone_orders_and_marks_apart(async_session) -> None:
    repo = await insert_repo(async_session)
    await _two_startup_causes(async_session, repo.id, evidence=True, unit_tests_only=True)
    order, rest, summary = await _queue_and_rows(async_session, repo.id)
    assert order == ["zzz_measured", "aaa_first"]
    # A test run is no workload a cause was "not exercised in".
    assert "evidence_workloads" not in summary
    marks = {i["intervention_symbol"].rpartition(":")[2]: i.get("runtime_evidence") for i in rest}
    assert marks == {
        "zzz_measured": {"state": "confirmed", "basis": "measured_in_tests"},
        "aaa_first": None,
    }


@pytest.mark.parametrize(
    ("evidence", "unit_tests_only", "admitted"),
    [(False, False, False), (True, False, True), (True, True, True)],
)
async def test_a_cost_measured_above_the_bar_admits_an_unsized_loop(
    async_session, evidence, unit_tests_only, admitted
) -> None:
    from sqlalchemy import select

    from repowise.core.persistence.crud import finalize_performance_opportunities

    repo = await insert_repo(async_session)
    await _two_startup_causes(
        async_session, repo.id, evidence=evidence, unit_tests_only=unit_tests_only
    )
    await finalize_performance_opportunities(async_session, repo.id)
    rows = (await async_session.execute(select(PerformanceOpportunity))).scalars().all()
    eligible = {r.intervention_symbol.rpartition(":")[2]: r.queue_eligible for r in rows}
    # Its loop size is unknown: only a measured cost above the bar admits it.
    assert eligible == {"aaa_first": True, "zzz_measured": admitted}


async def test_a_measured_function_no_static_cause_owns_becomes_one_item(async_session) -> None:
    from sqlalchemy import select

    from repowise.core.persistence.crud import finalize_performance_opportunities
    from repowise.core.persistence.crud.analysis.fix_first import load_fix_first, without_sources

    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    await _two_startup_causes(async_session, repo.id, evidence=True)
    await replace_workload_hotness(
        async_session,
        repo.id,
        [
            # Owned by a static cause: that cause is promoted, no second item.
            {**_hot("x"), "symbol_id": "src/svc.py::zzz_measured", "tier": "lead_candidate"},
            # No static cause: a measured hotspot.
            {**_hot("load", rank=2), "tier": "lead_candidate"},
            # Ranked below the bar, or above it in a unit-test run: nothing.
            {**_hot("helper", rank=3)},
            {**_hot("thin", run="r3"), "tier": "lead_candidate"},
        ],
    )
    await finalize_performance_opportunities(async_session, repo.id)

    rows = (await async_session.execute(select(PerformanceOpportunity))).scalars().all()
    hotspots = [r for r in rows if r.biomarker_type == "measured_hotspot"]
    assert [r.intervention_symbol for r in hotspots] == [f"{APP}::load"]
    (item,) = hotspots
    assert (item.file_path, item.execution_context) == (APP, "production")
    assert (item.plan_state, item.fix_strategy, item.cost_proof) == (
        "available",
        "reduce_measured_self_time",
        "proven",
    )
    assert item.queue_eligible
    assert json.loads(item.details_json)["facets"]["confirmed"] == "measured"

    queue = await load_fix_first(async_session, repo.id, limit=None)
    assert item.opportunity_id in {i.source.opportunity_id for i in queue.items}

    # The agent surfaces' cut: the item goes, the rest re-rank and recount.
    hidden = without_sources(queue, frozenset({item.opportunity_id}))
    assert item.opportunity_id not in {i.source.opportunity_id for i in hidden.items}
    assert [i.rank for i in hidden.items] == list(range(1, len(queue.items)))
    assert hidden.totals.eligible == queue.totals.eligible - 1
    assert hidden.by_improves["performance"] == queue.by_improves["performance"] - 1


async def test_a_measured_hotspots_evidence_on_rest_is_the_run_that_measured_it(
    async_session,
) -> None:
    from sqlalchemy import select

    from repowise.core.persistence.crud import finalize_performance_opportunities
    from repowise.server.routers.code_health.performance_routes import (
        get_performance_opportunity_detail,
        list_performance_opportunity_findings,
    )

    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    await _two_startup_causes(async_session, repo.id, evidence=True)
    await replace_workload_hotness(
        async_session, repo.id, [{**_hot("load", rank=2), "tier": "lead_candidate"}]
    )
    # The function's basis row, the one the drawer's measured runs read.
    await replace_symbol_hotness(
        async_session,
        repo.id,
        [
            {
                **_hot("load", rank=2),
                "tier": "lead_candidate",
                "workload_name": "w-r1",
                "workload_class": "entry_point",
                "measured_at": NOW,
            }
        ],
    )
    await finalize_performance_opportunities(async_session, repo.id)
    rows = (await async_session.execute(select(PerformanceOpportunity))).scalars().all()
    (item,) = [r for r in rows if r.biomarker_type == "measured_hotspot"]
    static = next(r for r in rows if r.biomarker_type != "measured_hotspot")

    page = await list_performance_opportunity_findings(
        repo.id, item.opportunity_id, limit=50, offset=0, session=async_session
    )
    assert (page["total"], page["has_more"]) == (1, False)
    (row,) = page["items"]
    assert (row["file_path"], row["function_name"]) == (APP, "load")
    # Function, rank, share with its interval and the workload, from the run.
    assert row["reason"].startswith("Measured: rank 2 ")
    assert "w-r1" in row["reason"] and "10%" in row["reason"]
    assert (
        await list_performance_opportunity_findings(
            repo.id, item.opportunity_id, limit=50, offset=1, session=async_session
        )
    )["items"] == []

    detail = await get_performance_opportunity_detail(
        repo.id, item.opportunity_id, evidence_limit=8, evidence_offset=0, session=async_session
    )
    assert detail["evidence_total"] == 1
    assert detail["evidence"][0]["reason"] == row["reason"]

    # A static cause keeps its own observations and is never given a run.
    other = await list_performance_opportunity_findings(
        repo.id, static.opportunity_id, limit=50, offset=0, session=async_session
    )
    assert all(r["biomarker_type"] != "measured_hotspot" for r in other["items"])


async def test_a_one_line_python_function_is_unknown_to_the_map(async_session) -> None:
    repo = await insert_repo(async_session)
    async_session.add_all(
        _map(repo.id, [("tests/test_app.py::test_imports", "tests/test_app.py")], ["1-3,90"])
    )
    await async_session.flush()
    spans = {"one": (APP, 90, 90), "body": (APP, 1, 3), "elsewhere": ("src/other.py", 1, 9)}

    found = await spans_run(async_session, repo.id, spans)

    # Its def line runs on import: no line tells a call apart, so no claim either way.
    assert "one" not in found and "elsewhere" not in found
    assert found["body"] == [("tests/test_app.py::test_imports", "tests/test_app.py", 2)]


async def test_a_stored_benchmark_among_the_nearest_tests_is_never_listed(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    timed = "tests/test_app.py::test_timed"
    async_session.add_all(
        [
            _run(repo.id, "r1"),
            FunctionFact(
                repository_id=repo.id,
                symbol_id=f"{APP}::load",
                tests_json=encode_tests(
                    [MappedTest(timed, 2), MappedTest("tests/test_load.py::test_load", 2)]
                ),
            ),
            EvidenceBenchmarkResult(
                run_id="r1", benchmark_key="test_timed", repository_id=repo.id,
                symbol_id=timed, median=1.0,
            ),
        ]
    )
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("load")])

    row = (await measured_costs(async_session, repo.id, now=NOW))["measured"]["rows"][0]

    assert (row["tests"], row["tests_basis"]) == (["tests/test_load.py::test_load"], "call_graph")


async def test_benchmarks_list_each_result_with_the_function_it_measures(async_session) -> None:
    from repowise.core.persistence.crud import unranked_reason

    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    bench = "benchmarks/test_app.py::test_load"
    # A benchmark-class profile is stored too: it never ranks, so it is no runtime data.
    async_session.add(_run(repo.id, "p1", cls="benchmark"))
    run = _run(repo.id, "b1", cls="benchmark")
    run.kind = "benchmark"
    async_session.add(run)
    async_session.add_all(
        [
            GraphEdge(repository_id=repo.id, source_node_id=bench, target_node_id=target,
                      edge_type="calls")
            # The fixture's helper is no production code, so it measures nothing.
            for target in (f"{APP}::load", "benchmarks/conftest.py::load")
        ]
    )
    await async_session.flush()
    async_session.add_all(
        [
            EvidenceBenchmarkResult(
                run_id="b1", repository_id=repo.id, benchmark_key=f"{bench}[small]",
                symbol_id=bench, unit="s", median=0.002, allocs_per_op=None,
            ),
            EvidenceBenchmarkResult(
                run_id="b1", repository_id=repo.id, benchmark_key="other", symbol_id=None,
                unit="s", median=0.5,
            ),
        ]
    )
    await async_session.flush()
    view = await measured_costs(async_session, repo.id, now=NOW, limit=5)
    assert set(view) == {"benchmarks"}
    group = view["benchmarks"]
    assert group["total"] == 2
    assert [r["run_id"] for r in group["runs"]] == ["b1"]
    assert [(r["benchmark"], r["measures"], r["time"]) for r in group["rows"]] == [
        ("test_load[small]", f"{APP}::load", 0.002),
        ("other", None, 0.5),
    ]
    assert await unranked_reason(async_session, repo.id) == "no_runtime_data"
    # Another run's id keeps nothing.
    assert await measured_costs(async_session, repo.id, now=NOW, run_id="r1") is None


async def test_a_benchmark_profile_never_reads_as_evidence_ahead_of_the_index(async_session) -> None:
    from repowise.core.persistence.crud.analysis.evidence import _newest_ahead_of_index

    repo = await insert_repo(async_session)
    repo.head_commit = "c" * 40
    at_head = _run(repo.id, "p1", day=1)
    at_head.ingested_at = datetime(2026, 11, 1, tzinfo=UTC)
    bench = _run(repo.id, "p2", cls="benchmark", day=2)
    bench.workload_name = at_head.workload_name
    bench.commit_sha = "d" * 40
    bench.ingested_at = datetime(2026, 11, 2, tzinfo=UTC)
    async_session.add_all([at_head, bench])
    await async_session.flush()
    assert await _newest_ahead_of_index(async_session, repo.id) is False


async def test_an_older_benchmark_run_id_keeps_nothing(async_session) -> None:
    from repowise.core.persistence.models import EvidenceBenchmarkResult

    repo = await insert_repo(async_session)
    for rid, day in (("b0", 1), ("b1", 2)):
        run = _run(repo.id, rid, cls="benchmark", day=day)
        run.kind, run.workload_name = "benchmark", "go bench"
        async_session.add(run)
    await async_session.flush()
    async_session.add_all(
        EvidenceBenchmarkResult(run_id=rid, repository_id=repo.id, benchmark_key="k", median=1.0)
        for rid in ("b0", "b1")
    )
    await async_session.flush()
    assert await measured_costs(async_session, repo.id, now=NOW, run_id="b0") is None
    view = await measured_costs(async_session, repo.id, now=NOW, run_id="b1")
    assert [r["run_id"] for r in view["benchmarks"]["rows"]] == ["b1"]


async def test_an_import_row_is_listed_as_import_time_with_no_lines(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    module = f"{APP}::<module>"
    await replace_workload_hotness(
        async_session, repo.id, [{**_hot("serve", rank=2), "symbol_id": module}, _hot("serve")]
    )

    rows = {r["symbol_id"]: r for r in (await measured_costs(async_session, repo.id, now=NOW))[
        "measured"]["rows"]}

    imported = rows[module]
    assert (imported["label"], imported["file_path"], imported["lines"]) == (
        "import time", APP, None,
    )
    assert imported["callers"] == [] and imported["owner"] is None
    # A function's row carries no label: its name says what it is.
    assert "label" not in rows[f"{APP}::serve"]


async def test_a_row_carries_the_step_its_run_measured_inside_it(async_session) -> None:
    """The run measured stat calls per entry inside ``serve``: its row says what
    to do, why, and the one verify command; a row with nothing inside stays silent."""
    from repowise.core.evidence.compare import Cause, ChangedCommit, Comparison

    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    inside = [
        {"name": "is_file", "kind": "library", "share": 0.6,
         "leaf": {"name": "stat", "kind": "native", "share": 0.6}},
        {"name": "rglob", "kind": "library", "share": 0.3},
    ]
    async_session.add(_run(repo.id, "r1"))
    await async_session.flush()
    async_session.add(
        EvidenceSymbolCost(
            run_id="r1", symbol_id=f"{APP}::serve", repository_id=repo.id,
            inside_json=json.dumps(inside),
        )
    )
    await async_session.flush()
    await replace_workload_hotness(async_session, repo.id, [_hot("serve"), _hot("load", rank=2)])
    slower = Comparison(
        kind="profile", workload="w-r1", environment="local", verdict="slower", band_runs=3,
        head_run_id="r1", current_runs=1, base_commit="a" * 40, head_commit="c" * 40,
        cause=Cause("a" * 40, "c" * 40, 2, (ChangedCommit("f" * 40, None, 42, (f"{APP}::serve",)),)),
    )

    rows = (
        await measured_costs(async_session, repo.id, now=NOW, comparisons=[slower])
    )["measured"]["rows"]
    serve, load = rows

    assert serve["next_step"]["rule"] == "filesystem"
    assert "os.scandir" in serve["next_step"]["next"] and "verify" not in serve["next_step"]
    assert serve["sources"]["next_step"] == "run_inside"
    assert serve["slower"]["changed_in"] == [{"sha": "f" * 40, "pr": 42}]
    assert load["next_step"] is None and "slower" not in load
    assert serve["verify_basis"] == load["verify_basis"] == "workload"


async def _measured_items(session, cls: str = "entry_point") -> tuple[str, list]:
    """``serve`` and ``load`` measured above the bar by one *cls* run, a step
    firing on ``serve`` only (its run measured stat calls per entry inside it)."""
    from sqlalchemy import select

    from repowise.core.persistence.crud import finalize_performance_opportunities

    repo = await insert_repo(session)
    await _seed_code(session, repo.id)
    inside = [
        {"name": "is_file", "kind": "library", "share": 0.6,
         "leaf": {"name": "stat", "kind": "native", "share": 0.6}},
        {"name": "rglob", "kind": "library", "share": 0.3},
    ]
    session.add(_run(repo.id, "r1", cls=cls))
    await session.flush()
    session.add(
        EvidenceSymbolCost(
            run_id="r1", symbol_id=f"{APP}::serve", repository_id=repo.id,
            inside_json=json.dumps(inside),
        )
    )
    await session.flush()
    await replace_workload_hotness(
        session,
        repo.id,
        [
            {**_hot("load"), "tier": "lead_candidate", "self_share": 0.2},
            {**_hot("serve", rank=2), "tier": "lead_candidate"},
        ],
    )
    await finalize_performance_opportunities(session, repo.id)
    rows = (await session.execute(select(PerformanceOpportunity))).scalars().all()
    return repo.id, [r for r in rows if r.biomarker_type == "measured_hotspot"]


async def test_a_measured_item_with_a_next_step_leads_fix_first_with_its_row(
    async_session,
) -> None:
    from repowise.core.persistence.crud.analysis.fix_first import load_fix_first
    from repowise.core.persistence.crud.analysis.performance import measured_hotspot_ids

    repo_id, items = await _measured_items(async_session)
    by_symbol = {r.intervention_symbol: r for r in items}
    serve, load = by_symbol[f"{APP}::serve"], by_symbol[f"{APP}::load"]
    stored = json.loads(serve.details_json)
    assert stored["may_lead"] is True
    assert stored["measured_row"]["next_step"]["rule"] == "filesystem"
    # No rule fired on ``load``: listed, never first, though its share is larger.
    assert json.loads(load.details_json)["may_lead"] is False

    queue = await load_fix_first(async_session, repo_id, limit=None)
    lead = queue.lead
    assert lead.source.opportunity_id == serve.opportunity_id
    assert lead.title == "Speed up serve"
    assert "os.scandir" in lead.action.steps[0].text
    assert lead.gain.text == 'up to 10% of the "w-r1" run'
    assert lead.verify.command.startswith("repowise perf measure --save before -- w-r1")
    assert [t.path for t in lead.verify.tests] == ["tests/test_app.py::test_serve"]
    assert ("self time", '10% of the "w-r1" run') in [(f.label, f.value) for f in lead.facts]
    compact = lead.compact()
    assert "os.scandir" in compact["first_step"]["action"] and compact["verify"]["command"]

    # The agent surfaces keep the item with a step and drop the one without.
    assert await measured_hotspot_ids(async_session, repo_id, without_step=True) == {
        load.opportunity_id
    }


@pytest.mark.parametrize("cls", ["unit_test", "benchmark"])
async def test_unit_test_and_benchmark_runs_mint_no_measured_item(async_session, cls) -> None:
    _, items = await _measured_items(async_session, cls)
    assert items == []


async def test_a_failed_row_read_keeps_the_health_write_and_the_item_never_leads(
    async_session, monkeypatch
) -> None:
    from repowise.core.persistence.crud.analysis import performance

    async def broken(*args, **kwargs):
        raise RuntimeError("no rows today")

    monkeypatch.setattr(performance, "measured_costs", broken)
    _, items = await _measured_items(async_session)
    assert len(items) == 2
    assert all(json.loads(r.details_json)["may_lead"] is False for r in items)
    assert all("measured_row" not in json.loads(r.details_json) for r in items)


def _statement_run(repo_id: str, run_id: str, *, day: int = 16) -> EvidenceRun:
    return EvidenceRun(
        id=run_id, repository_id=repo_id, run_key=run_id, kind="statement_stats",
        format="repowise-evidence-v1", source="plugin", workload_name="orders-db",
        workload_class="production", value_kind="wall", unit="ms", environment="prod",
        commit_sha="d" * 40, measured_at=datetime(2026, 11, day, tzinfo=UTC),
        window_start=datetime(2026, 11, 1, tzinfo=UTC),
        window_end=datetime(2026, 11, day, tzinfo=UTC),
    )


def _statement(repo_id: str, run_id: str, fp: str, symbol: str | None, issuer: str | None,
               calls: int, total_ms: float) -> EvidenceStatementStats:
    return EvidenceStatementStats(
        run_id=run_id, statement_fp=fp, repository_id=repo_id, query_text="select $1",
        symbol_id=f"{APP}::{symbol}" if symbol else None, issuer=issuer, calls=calls,
        total_ms=total_ms, mean_ms=total_ms / calls,
    )


async def _seed_statements(session, repo_id: str, *, joined: bool = True) -> None:
    session.add(_statement_run(repo_id, "s1"))
    await session.flush()
    rows = [
        _statement(repo_id, "s1", "a" * 16, None, "ambiguous", 3, 300.0),
        _statement(repo_id, "s1", "b" * 16, None, "database_schedule", 1, 100.0),
    ]
    if joined:
        rows += [
            _statement(repo_id, "s1", "c" * 16, "serve", "function", 40, 400.0),
            _statement(repo_id, "s1", "d" * 16, "serve", "function", 10, 100.0),
            _statement(repo_id, "s1", "e" * 16, "load", "function", 100, 100.0),
        ]
    session.add_all(rows)
    await session.flush()


async def test_production_statements_list_as_action_rows_that_never_lead(async_session) -> None:
    """A production statement run with no profile at all: each function the
    join named carries its statements as a J4 row (share of the window's
    statement time, calls, mean, verify on the next window); the statements
    on no single function are counted by label, never put on one."""
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    await _seed_statements(async_session, repo.id)
    assert await measured_status(async_session, repo.id) == "measured"

    view = await measured_costs(async_session, repo.id, now=NOW)
    assert set(view) == {"statements"}
    group = view["statements"]
    assert group["total"] == 2
    (run,) = group["runs"]
    assert (run["run_id"], run["workload_class"], run["total_ms"], run["statements"]) == (
        "s1", "production", 1000.0, 5,
    )
    assert run["window_start"].startswith("2026-11-01") and run["age_days"] == 2
    assert run["window"]["claim"] == (
        f"in production over 2026-11-01 to 2026-11-16, deployed {'d' * 12}"
    )
    assert run["unattributed"] == {
        "ambiguous": {"statements": 1, "total_ms": 300.0, "share": 0.3},
        "database_schedule": {"statements": 1, "total_ms": 100.0, "share": 0.1},
    }
    serve, load = group["rows"]
    assert (serve["symbol_id"], serve["rank"], serve["share"]) == (f"{APP}::serve", 1, 0.5)
    assert serve["statements"] == {"calls": 50, "total_ms": 500.0, "mean_ms": 10.0, "statements": 2}
    assert serve["verify"] == "compare this row in the next orders-db window"
    assert serve["verify_basis"] == "workload" and serve["next_step"] is None
    # The function's context, as on a profile row.
    assert serve["lines"] == [1, 20] and serve["callers_total"] == 1
    assert serve["owner"]["name"] == "Grace"
    assert serve["sources"]["share"] == "statement_stats"
    assert (load["rank"], load["share"]) == (2, 0.1)

    # One function's rows (a measured hotspot's) and one run.
    only = await measured_costs(async_session, repo.id, now=NOW, symbol_ids={f"{APP}::load"})
    assert [r["symbol_id"] for r in only["statements"]["rows"]] == [f"{APP}::load"]
    assert await measured_costs(async_session, repo.id, now=NOW, run_id="nope") is None
    assert (await measured_costs(async_session, repo.id, now=NOW, run_id="s1"))["statements"]
    # ``limit=0`` keeps the run and the total with no rows.
    empty = (await measured_costs(async_session, repo.id, now=NOW, limit=0))["statements"]
    assert (empty["rows"], empty["total"], len(empty["runs"])) == ([], 2, 1)


async def test_a_profile_comparison_never_marks_a_statement_row_slower(async_session) -> None:
    """A slower profile of the same workload name measures something else."""
    from repowise.core.evidence.compare import Comparison, Slower

    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    await _seed_statements(async_session, repo.id)
    serve = f"{APP}::serve"
    slower = Comparison(
        kind="profile", workload="orders-db", environment="prod", verdict="slower",
        band_runs=3, head_run_id="r1", current_runs=1, base_commit="a" * 40,
        head_commit="b" * 40,
        slower=(Slower("self_seconds", serve, 1.0, 2.0, 2.0, "s", symbol_id=serve),),
        cause=None,
    )
    view = await measured_costs(async_session, repo.id, now=NOW, comparisons=[slower])
    assert all("slower" not in r for r in view["statements"]["rows"])


async def test_statements_on_no_single_function_rank_nothing_but_are_runtime_data(
    async_session,
) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    assert await unranked_reason(async_session, repo.id) == "no_runtime_data"
    await _seed_statements(async_session, repo.id, joined=False)
    # The window still lists its run, so where its time went shows; no rows.
    group = (await measured_costs(async_session, repo.id, now=NOW))["statements"]
    assert (group["rows"], group["total"]) == ([], 0)
    (run,) = group["runs"]
    assert {k: v["statements"] for k, v in run["unattributed"].items() if v["statements"]} == {
        "ambiguous": 1, "database_schedule": 1,
    }
    # A hotspot's rows never list a run that names no function.
    assert await measured_costs(async_session, repo.id, now=NOW, symbol_ids={f"{APP}::serve"}) is None
    assert await measured_status(async_session, repo.id) == "not_ranked"


def _route_run(repo_id: str, run_id: str) -> EvidenceRun:
    return EvidenceRun(
        id=run_id, repository_id=repo_id, run_key=run_id, kind="route_latency",
        format="repowise-evidence-v1", source="plugin", workload_name="api",
        workload_class="production", value_kind="wall", unit="ms", environment="prod",
        commit_sha="d" * 40, measured_at=datetime(2026, 11, 16, tzinfo=UTC),
        window_start=datetime(2026, 11, 1, tzinfo=UTC),
        window_end=datetime(2026, 11, 16, tzinfo=UTC),
    )


async def _seed_routes(session, repo_id: str, *, joined: bool = True) -> None:
    session.add(_route_run(repo_id, "q1"))
    await session.flush()

    def row(method, route, handler, count, total_ms, p95=None, errors=None):
        return EvidenceRouteLatency(
            run_id="q1", method=method, route=route, repository_id=repo_id,
            handler_symbol_id=f"{APP}::{handler}" if handler else None,
            request_count=count, p50_ms=None, p95_ms=p95, total_ms=total_ms,
            error_count=errors,
        )

    rows = [row("GET", "/x", None, 5, 200.0)]
    if joined:
        rows += [
            row("GET", "/serve/:id", "serve", 30, 600.0, 90.0, 3),
            row("GET", "/load", "load", 20, 200.0, 20.0, 0),
        ]
    session.add_all(rows)
    await session.flush()


async def test_production_routes_list_as_handler_rows_that_never_lead(async_session) -> None:
    """A production route run with no profile: each handler the route join
    named carries its requests as a J4 row (share of the window's request
    time, requests, mean, p95, verify on the next window); routes on no
    handler are counted, never put on one."""
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    await _seed_routes(async_session, repo.id)
    assert await measured_status(async_session, repo.id) == "measured"

    view = await measured_costs(async_session, repo.id, now=NOW)
    assert set(view) == {"routes"}
    group = view["routes"]
    assert group["total"] == 2
    (run,) = group["runs"]
    assert (run["run_id"], run["total_ms"], run["requests"], run["routes"]) == ("q1", 1000.0, 55, 3)
    assert run["window"]["claim"].startswith("in production over 2026-11-01 to 2026-11-16")
    assert run["unattributed"] == {"unjoined": {"routes": 1, "total_ms": 200.0, "share": 0.2}}
    serve, load = group["rows"]
    assert (serve["symbol_id"], serve["rank"], serve["share"]) == (f"{APP}::serve", 1, 0.6)
    assert serve["routes"] == {
        "requests": 30, "total_ms": 600.0, "mean_ms": 20.0, "p95_ms": 90.0, "errors": 3,
        "routes": ["GET /serve/:id"],
    }
    assert serve["verify"] == "compare this row in the next api window"
    assert serve["next_step"] is None and "statements" not in serve
    assert serve["sources"]["share"] == "route_latency"
    assert serve["lines"] == [1, 20] and serve["owner"]["name"] == "Grace"
    assert (load["rank"], load["share"]) == (2, 0.2)
    only = await measured_costs(async_session, repo.id, now=NOW, symbol_ids={f"{APP}::load"})
    assert [r["symbol_id"] for r in only["routes"]["rows"]] == [f"{APP}::load"]
    empty = (await measured_costs(async_session, repo.id, now=NOW, limit=0))["routes"]
    assert (empty["rows"], empty["total"], len(empty["runs"])) == ([], 2, 1)


async def test_routes_on_no_handler_rank_nothing_but_are_runtime_data(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    await _seed_routes(async_session, repo.id, joined=False)
    assert await measured_costs(async_session, repo.id, now=NOW) is None
    assert await measured_status(async_session, repo.id) == "not_ranked"


async def test_a_view_without_route_runs_has_no_routes_key(async_session) -> None:
    """Every other view (and the payload built from it) is unchanged."""
    from repowise.core.evidence.payloads import measured_payload

    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    await _seed_statements(async_session, repo.id)
    view = await measured_costs(async_session, repo.id, now=NOW)
    assert "routes" not in view
    assert "routes" not in measured_payload(view, "measured")
    assert "routes" not in measured_payload(None, "no_runtime_data")
