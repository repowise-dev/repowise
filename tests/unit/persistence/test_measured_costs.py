"""``crud.measured_costs``: what each workload's newest run measured, beside the static causes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert

from repowise.core.analysis.function_test_map import MappedTest, encode_tests
from repowise.core.persistence.crud import measured_costs, replace_workload_hotness
from repowise.core.persistence.models import (
    EvidenceRun,
    FunctionFact,
    GitMetadata,
    GraphEdge,
    GraphNode,
    HealthFinding,
    PerformanceOpportunity,
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
            GitMetadata(
                repository_id=repo_id,
                file_path=APP,
                primary_owner_name="Ada",
                primary_owner_commit_pct=0.8,
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
            _run(repo.id, "r2", cls="benchmark", day=16),
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
            # The benchmark ranks ``serve`` too: listed under both workloads.
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
    assert serve["owner"] == {"name": "Ada", "commit_pct": 0.8}
    assert serve["tests"] == ["tests/test_app.py::test_serve"]
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
        "outside_production": 4,
        "value_kind": "wall",
        "ordering_only": False,
    }
    assert [r["run_id"] for r in measured["runs"]] == ["r2", "r1"]

    tests_group = view["measured_in_tests"]
    assert [r["symbol_id"] for r in tests_group["rows"]] == [f"{APP}::thin"]
    assert [r["workload_class"] for r in tests_group["runs"]] == ["unit_test"]
    # Never mapped to tests: unknown, not "no tests".
    assert tests_group["rows"][0]["tests"] is None


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
    async_session.add_all([_run(repo.id, "r1"), _run(repo.id, "r2", cls="benchmark", day=16)])
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
