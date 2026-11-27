"""/api/repos/{repo_id}/evidence/*: status, runs, ranked rows and the per-function view."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from repowise.core.persistence.crud import replace_symbol_hotness, replace_workload_hotness
from repowise.core.persistence.models import EvidenceRun, Repository
from repowise.server.routers import runtime_evidence
from tests.unit.server.conftest import create_test_repo

HEAD = "a" * 40
LOGIN = "src/auth/service.py::login"
LOAD = "src/auth/service.py::load"
MEASURED_AT = datetime(2026, 11, 15, tzinfo=UTC)
#: Run id -> (workload class, commit).
RUNS = {"r1": ("entry_point", HEAD), "r2": ("unit_test", "b" * 40)}


@pytest.fixture
async def ev_client(app, client: AsyncClient) -> AsyncClient:
    app.include_router(runtime_evidence.router)
    return client


def _ranked(symbol_id: str, run: str, rank: int, **over) -> dict:
    return {
        "run_id": run,
        "symbol_id": symbol_id,
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


async def _repo(client: AsyncClient, session_factory) -> str:
    repo_id = (await create_test_repo(client))["id"]
    async with session_factory() as session:
        await session.execute(
            update(Repository).where(Repository.id == repo_id).values(head_commit=HEAD)
        )
        await session.commit()
    return repo_id


async def _seed(session_factory, repo_id: str) -> None:
    async with session_factory() as session:
        session.add_all(
            EvidenceRun(
                id=run_id,
                repository_id=repo_id,
                run_key=run_id,
                kind="profile",
                format="repowise-evidence-v1",
                source="cli",
                workload_name=f"w-{run_id}",
                workload_class=cls,
                value_kind="wall",
                unit="samples",
                commit_sha=commit,
                measured_at=MEASURED_AT,
                total_value=1000.0,
                explained_value=950.0,
                repo_self_value=470.0,
                wait_value=300.0,
                library_value=120.0,
                unlisted_symbols=4,
                units_total=10,
                units_matched=9,
                units_skipped=1,
                skipped_json='{"no_symbol": 1}',
            )
            for run_id, (cls, commit) in RUNS.items()
        )
        await session.flush()
        await replace_symbol_hotness(
            session,
            repo_id,
            [
                {
                    "symbol_id": LOGIN,
                    "tier": "lead_candidate",
                    "rank": 1,
                    "self_share": 0.1,
                    "share_low": 0.08,
                    "share_high": 0.12,
                    "samples": 100,
                    "run_samples": 1000,
                    "run_id": "r1",
                    "run_count": 2,
                    "workload_name": "w-r1",
                    "workload_class": "entry_point",
                    "freshness": "current",
                    "ordering_only": False,
                    "measured_at": MEASURED_AT,
                    "repo_self_share": 0.47,
                },
                {"symbol_id": LOAD, "tier": "measured", "run_id": "r1", "freshness": "stale"},
                {"symbol_id": "src/other.py::f", "tier": "ranked", "freshness": "current"},
            ],
        )
        await replace_workload_hotness(
            session,
            repo_id,
            [
                _ranked(LOGIN, "r1", 1, tier="lead_candidate"),
                _ranked("src/app.py::run", "r1", 2),
                _ranked("src/app.py::t", "r2", 1, freshness="body_unchanged"),
            ],
        )
        await session.commit()


@pytest.mark.asyncio
async def test_unknown_repo_is_404(ev_client: AsyncClient) -> None:
    for path in ("status", "runs", "measured", "functions?file=a.py"):
        assert (await ev_client.get(f"/api/repos/nope/evidence/{path}")).status_code == 404


@pytest.mark.asyncio
async def test_no_evidence_reads_unknown_with_the_setup_command(
    ev_client: AsyncClient, session_factory
) -> None:
    repo_id = await _repo(ev_client, session_factory)

    status = (await ev_client.get(f"/api/repos/{repo_id}/evidence/status")).json()
    assert status == {
        "status": "no_runtime_data",
        "command": "repowise evidence setup --format json",
        "reference_commit": HEAD,
        "runs_stored": {},
        "workloads": [],
    }
    measured = (await ev_client.get(f"/api/repos/{repo_id}/evidence/measured")).json()
    assert measured == {
        "basis": "unknown",
        "status": "no_runtime_data",
        "command": "repowise evidence setup --format json",
        "measured": None,
        "measured_in_tests": None,
        "statements": None,
        "benchmarks": None,
    }
    assert (await ev_client.get(f"/api/repos/{repo_id}/evidence/runs")).json() == {
        "reference_commit": HEAD,
        "runs": [],
    }
    functions = await ev_client.get(f"/api/repos/{repo_id}/evidence/functions?symbol_id={LOGIN}")
    assert functions.json() == {"functions": []}


@pytest.mark.asyncio
async def test_status_and_runs_name_basis_and_freshness(
    ev_client: AsyncClient, session_factory
) -> None:
    repo_id = await _repo(ev_client, session_factory)
    await _seed(session_factory, repo_id)

    status = (await ev_client.get(f"/api/repos/{repo_id}/evidence/status")).json()
    assert (status["status"], status["command"]) == ("measured", None)
    assert status["runs_stored"] == {"profile": 2}
    by_run = {w["run_id"]: w for w in status["workloads"]}
    assert by_run["r1"]["class"] == "entry_point"
    assert by_run["r1"]["freshness"] == "current"
    assert by_run["r2"]["freshness"] == "stale"
    assert by_run["r1"]["repo_self_share"] == 0.47
    assert by_run["r1"]["units"]["skipped"] == 1
    assert by_run["r1"]["skipped"] == {"no_symbol": 1}

    runs = (await ev_client.get(f"/api/repos/{repo_id}/evidence/runs")).json()["runs"]
    assert sorted(r["run_id"] for r in runs) == ["r1", "r2"]


@pytest.mark.asyncio
async def test_measured_groups_by_class_and_filters_one_run(
    ev_client: AsyncClient, session_factory
) -> None:
    repo_id = await _repo(ev_client, session_factory)
    await _seed(session_factory, repo_id)

    body = (await ev_client.get(f"/api/repos/{repo_id}/evidence/measured?limit=1")).json()
    assert (body["basis"], body["status"]) == ("measured", "measured")
    lead = body["measured"]
    assert [(r["run_id"], r["rank"]) for r in lead["rows"]] == [("r1", 1)]
    assert lead["total"] == 2
    run = lead["runs"][0]
    assert (run["workload_class"], run["workload_name"], run["samples"]) == (
        "entry_point",
        "w-r1",
        1000,
    )
    assert run["repo_self_share"] == 0.47
    assert run["measured_at"] == "2026-11-15T00:00:00Z"
    row = lead["rows"][0]
    assert (row["share"], row["share_low"], row["share_high"]) == (0.1, 0.08, 0.12)
    assert row["on_static_findings"] == []
    assert [r["symbol_id"] for r in body["measured_in_tests"]["rows"]] == ["src/app.py::t"]

    one = (await ev_client.get(f"/api/repos/{repo_id}/evidence/measured?run_id=r2")).json()
    assert one["measured"] is None
    assert [r["run_id"] for r in one["measured_in_tests"]["runs"]] == ["r2"]

    # A run that ranks nothing here is not ranked, though others are.
    other = (await ev_client.get(f"/api/repos/{repo_id}/evidence/measured?run_id=old")).json()
    assert (other["basis"], other["status"], other["command"]) == ("unknown", "not_ranked", None)

    assert (
        await ev_client.get(f"/api/repos/{repo_id}/evidence/measured?limit=101")
    ).status_code == 422


@pytest.mark.asyncio
async def test_functions_by_symbol_and_by_file(ev_client: AsyncClient, session_factory) -> None:
    repo_id = await _repo(ev_client, session_factory)
    await _seed(session_factory, repo_id)
    base = f"/api/repos/{repo_id}/evidence/functions"

    assert (await ev_client.get(base)).status_code == 400

    one = (await ev_client.get(base, params={"symbol_id": LOGIN})).json()["functions"]
    assert len(one) == 1
    login = one[0]
    # The uncalibrated lead tier is never claimed: it reads as ranked.
    assert (login["tier"], login["rank"], login["workload_class"]) == ("ranked", 1, "entry_point")
    assert login["file_path"] == "src/auth/service.py"
    assert login["measured_at"] == "2026-11-15T00:00:00Z"

    in_file = (await ev_client.get(base, params={"file": "src\\auth\\service.py"})).json()
    assert [(f["symbol_id"], f["tier"], f["freshness"]) for f in in_file["functions"]] == [
        (LOAD, "measured", "stale"),
        (LOGIN, "ranked", "current"),
    ]


@pytest.mark.asyncio
async def test_functions_carry_the_next_step_with_a_production_loop_only(
    ev_client: AsyncClient, session_factory
) -> None:
    import json

    from repowise.core.persistence.models import HealthFinding

    repo_id = await _repo(ev_client, session_factory)
    await _seed(session_factory, repo_id)
    inside = [{"name": "Session.execute", "kind": "library", "share": 0.9}]
    drivers = [{"name": "list_users", "share": 1.0, "symbol": "src/api.py::list_users"}]

    def finding(path: str, loop_path: list[str]) -> HealthFinding:
        return HealthFinding(
            repository_id=repo_id, file_path=path, biomarker_type="io_in_loop", severity="medium",
            function_name=loop_path[0].rsplit("::", 1)[-1], dimension="performance", status="open",
            reason="a database call is reached once per loop iteration",
            details_json=json.dumps({"boundary_kind": "db", "cross_function": True, "path": loop_path}),
        )

    async with session_factory() as session:
        await replace_symbol_hotness(session, repo_id, [{
            "symbol_id": LOAD, "tier": "ranked", "run_id": "r1", "freshness": "current",
            "workload_name": "w-r1", "workload_class": "entry_point",
            "inside_json": json.dumps(inside), "drivers_json": json.dumps(drivers),
        }])
        # A loop in a test calls the driver: never an N+1 in the product.
        session.add(finding("tests/test_api.py", ["tests/test_api.py::test_many", "src/api.py::list_users"]))
        await session.commit()
    base = f"/api/repos/{repo_id}/evidence/functions"
    load = (await ev_client.get(base, params={"symbol_id": LOAD})).json()["functions"][0]
    assert load["next_step"]["rule"] == "db_read"
    assert "N+1" not in load["next_step"]["next"]
    assert load["next_step"]["verify"].startswith("repowise perf measure --save before -- w-r1")

    async with session_factory() as session:
        session.add(finding("src/jobs.py", ["src/jobs.py::sync_all", "src/api.py::list_users"]))
        await session.commit()
    load = (await ev_client.get(base, params={"symbol_id": LOAD})).json()["functions"][0]
    assert "loop in sync_all (N+1)" in load["next_step"]["next"]


@pytest.mark.parametrize(
    ("status", "command"),
    [
        ("measured", None),
        ("not_ranked", None),
        ("needs_update", "repowise update"),
        ("stale", "repowise evidence setup --format json"),
        ("no_runtime_data", "repowise evidence setup --format json"),
    ],
)
def test_unranked_command(status: str, command: str | None) -> None:
    from repowise.core.persistence.crud import unranked_command

    assert unranked_command(status) == command


@pytest.mark.asyncio
async def test_status_and_measured_name_the_same_newest_run(
    ev_client: AsyncClient, session_factory
) -> None:
    from datetime import timedelta

    repo_id = await _repo(ev_client, session_factory)
    await _seed(session_factory, repo_id)
    # An older profile of r1's workload, added again after r1 (newest ingest,
    # older measurement), and a newer partial one: the ranking keeps r1, so
    # status must too.
    async with session_factory() as session:
        session.add(
            EvidenceRun(
                id="r0",
                repository_id=repo_id,
                run_key="r0",
                kind="profile",
                format="repowise-evidence-v1",
                source="cli",
                workload_name="w-r1",
                workload_class="entry_point",
                commit_sha=HEAD,
                measured_at=MEASURED_AT - timedelta(days=3),
                ingested_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        # A newer run of the same workload that mapped too little to rank.
        session.add(
            EvidenceRun(
                id="rp",
                repository_id=repo_id,
                run_key="rp",
                kind="profile",
                format="repowise-evidence-v1",
                source="cli",
                workload_name="w-r1",
                workload_class="entry_point",
                commit_sha=HEAD,
                measured_at=MEASURED_AT + timedelta(days=1),
                mapping_partial=True,
            )
        )
        await session.commit()

    status = (await ev_client.get(f"/api/repos/{repo_id}/evidence/status")).json()
    measured = (await ev_client.get(f"/api/repos/{repo_id}/evidence/measured")).json()
    status_runs = {w["run_id"] for w in status["workloads"] if w["workload"] == "w-r1"}
    measured_runs = {r["run_id"] for r in measured["measured"]["runs"]}
    assert status_runs == measured_runs == {"r1"}


async def test_tests_lists_slow_tests_with_their_basis_run(
    ev_client: AsyncClient, session_factory
) -> None:
    from repowise.core.persistence.crud import save_evidence_run

    repo_id = await _repo(ev_client, session_factory)
    empty = (await ev_client.get(f"/api/repos/{repo_id}/evidence/tests")).json()
    assert (empty["status"], empty["command"]) == ("no_test_timings", "repowise evidence setup")
    async with session_factory() as session:
        run = {
            "run_key": "t1", "kind": "test_timing", "format": "junit", "source": "cli",
            "workload_name": "pytest", "workload_class": "unit_test", "commit_sha": HEAD,
        }
        rows = [{"test_id": f"tests/test_a.py::test_{i}", "duration_s": float(i)} for i in range(3)]
        await save_evidence_run(session, repo_id, run, rows, history_runs=5, max_runs=20)
        await session.commit()

    out = (await ev_client.get(f"/api/repos/{repo_id}/evidence/tests", params={"limit": 2})).json()

    assert out["status"] == "measured"
    assert [t["test_id"] for t in out["slow_tests"]] == ["tests/test_a.py::test_2", "tests/test_a.py::test_1"]
    assert out["runs"][0]["freshness"] == "current"
    assert out["cost"] == {
        "verdict": "small_suite", "seconds": 3.0, "timed": 3,
        "concentration": {"tests": 1, "share": round(2 / 3, 4)}, "clusters": [], "command": None,
    }


def test_the_suite_cost_model_serves_clusters_with_their_fixture() -> None:
    from repowise.core.evidence.suite_cost import Fixture, cost_fold
    from repowise.server.schemas.runtime_evidence import SuiteCost

    slow = {f"tests/test_s.py::t{i}": 10.0 for i in range(5)}
    rest = {f"tests/test_r.py::t{i}": 1.0 for i in range(20)}
    reach = {t: {"src/app.py", "src/store.py"} for t in slow} | {t: {"src/app.py"} for t in rest}
    fixtures = {t: {Fixture("indexed", "tests/test_s.py", 48)} for t in slow}

    cost = cost_fold(slow | rest, reach, fixtures)

    served = SuiteCost.model_validate(cost).model_dump()
    assert served == cost
    assert served["clusters"][0]["fixture"]["name"] == "indexed"


@pytest.mark.asyncio
async def test_routes_return_what_the_payload_builders_make_from_the_same_rows(
    ev_client: AsyncClient, session_factory
) -> None:
    """A host that reads the same rows elsewhere returns the same payloads."""
    from repowise.core.evidence.payloads import (
        functions_payload,
        measured_payload,
        runs_payload,
        status_payload,
    )
    from repowise.core.persistence import crud
    from repowise.server.schemas.runtime_evidence import (
        EvidenceRunsResponse,
        EvidenceStatusResponse,
        MeasuredFunctionsResponse,
        MeasuredResponse,
    )

    repo_id = await _repo(ev_client, session_factory)
    await _seed(session_factory, repo_id)
    async with session_factory() as session:
        runs = await crud.list_evidence_runs(session, repo_id)
        status = await crud.measured_status(session, repo_id)
        view = await crud.measured_costs(session, repo_id, limit=10)
        none = await crud.measured_costs(session, repo_id, limit=10, run_id="nope")
        rows = await crud.hotness_for_symbols(session, repo_id, [], files=["src/auth/service.py"])
        findings = await crud.step_findings(session, repo_id, rows.values())
        built = {
            "status": EvidenceStatusResponse(**status_payload(runs, status, HEAD)),
            "runs": EvidenceRunsResponse(**runs_payload(runs, HEAD)),
            "measured": MeasuredResponse(**measured_payload(view, status)),
            "measured?run_id=nope": MeasuredResponse(**measured_payload(none, status)),
            "functions?file=src/auth/service.py": MeasuredFunctionsResponse(
                **functions_payload(rows, findings)
            ),
        }
    for path, model in built.items():
        served = (await ev_client.get(f"/api/repos/{repo_id}/evidence/{path}")).json()
        assert served == model.model_dump(mode="json", by_alias=True), path


def test_the_measured_row_schema_holds_the_action_row_json_form() -> None:
    """Every key ``evidence.action_row`` writes is a ``MeasuredRow`` field (closed models)."""
    from types import SimpleNamespace

    from repowise.core.evidence.action_row import action_row, row_step
    from repowise.core.evidence.compare import Cause, ChangedCommit, Comparison, Slower
    from repowise.core.evidence.function_tests import RowTests
    from repowise.server.schemas.runtime_evidence import MeasuredRow

    fn = "src/app.py::scan"
    inside = [{"name": "is_file", "kind": "library", "share": 0.8}]
    measured = SimpleNamespace(
        symbol_id=fn, run_id="r1", rank=1, self_share=0.2, share_low=0.1, share_high=0.3,
        total_share=0.4, samples=50, freshness="current",
    )
    slower = Comparison(
        kind="profile", workload="w", environment="local", verdict="slower", band_runs=3,
        head_run_id="r1", current_runs=1, base_commit="a" * 40, head_commit="b" * 40,
        slower=(Slower("self_seconds", fn, 1.0, 2.0, 2.0, "s", symbol_id=fn),),
        cause=Cause("a" * 40, "b" * 40, 1, (ChangedCommit("c" * 40, None, None, (fn,)),)),
    )
    row = action_row(
        measured, lines=(1, 9), workload_name="w", workload_class="entry_point",
        next_step=row_step(fn, inside, None, "w", "entry_point"),
        tests=RowTests(["tests/test_app.py::test_scan"], "per_test_map"),
        history={"owner": {"name": "Ada", "line_pct": 1.0}, "last_change": None},
        compare=[slower], static_causes={"perf3_x"},
    ).as_dict()

    parsed = MeasuredRow.model_validate(row)
    assert parsed.next_step is not None and parsed.next_step.verify is None
    assert parsed.verify_basis == "workload" and parsed.slower.ratio == 2.0
    assert parsed.model_dump(exclude_unset=True).keys() == row.keys()
    # A module body's row carries its import-time label through the closed model.
    module = action_row(SimpleNamespace(**{**vars(measured), "symbol_id": "src/app.py::<module>"}))
    assert MeasuredRow.model_validate(module.as_dict()).label == "import time"


@pytest.mark.asyncio
async def test_production_statements_are_measured_rows_on_the_measured_route(
    ev_client: AsyncClient, session_factory
) -> None:
    """A connected statement source and no profile: status says measured, and
    each function the join named is a row with its calls and the next window
    as its verify; the rest is counted by label, never on a function."""
    from repowise.core.persistence.models import EvidenceStatementStats

    repo_id = await _repo(ev_client, session_factory)
    async with session_factory() as session:
        session.add(
            EvidenceRun(
                id="s1", repository_id=repo_id, run_key="s1", kind="statement_stats",
                format="repowise-evidence-v1", source="plugin", workload_name="orders-db",
                workload_class="production", value_kind="wall", unit="ms", environment="prod",
                commit_sha=HEAD, measured_at=MEASURED_AT,
                window_start=datetime(2026, 11, 1, tzinfo=UTC), window_end=MEASURED_AT,
            )
        )
        await session.flush()
        session.add_all(
            [
                EvidenceStatementStats(
                    run_id="s1", statement_fp="a" * 16, repository_id=repo_id, symbol_id=LOGIN,
                    issuer="function", calls=8, total_ms=80.0, mean_ms=10.0,
                ),
                EvidenceStatementStats(
                    run_id="s1", statement_fp="b" * 16, repository_id=repo_id, symbol_id=None,
                    issuer="ambiguous", calls=2, total_ms=20.0, mean_ms=10.0,
                ),
            ]
        )
        await session.commit()

    status = (await ev_client.get(f"/api/repos/{repo_id}/evidence/status")).json()
    assert (status["status"], status["command"]) == ("measured", None)
    body = (await ev_client.get(f"/api/repos/{repo_id}/evidence/measured")).json()
    assert (body["basis"], body["status"], body["measured"]) == ("measured", "measured", None)
    group = body["statements"]
    (row,) = group["rows"]
    assert (row["symbol_id"], row["share"], row["freshness"]) == (LOGIN, 0.8, None)
    assert row["statements"] == {"calls": 8, "total_ms": 80.0, "mean_ms": 10.0, "statements": 1}
    assert (row["verify"], row["verify_basis"]) == (
        "compare this row in the next orders-db window",
        "workload",
    )
    (run,) = group["runs"]
    assert run["window_start"].startswith("2026-11-01")
    assert run["unattributed"] == {"ambiguous": {"statements": 1, "total_ms": 20.0, "share": 0.2}}


def test_a_statement_row_reads_as_statements_in_every_form() -> None:
    """The JSON form validates against the closed ``StatementRow``; the text and
    MCP forms say calls and mean, not a profile's self share."""
    from repowise.core.evidence.action_row import (
        action_row,
        row_compact,
        row_text,
        statement_facts,
    )
    from repowise.core.evidence.measured_fold import StatementCost
    from repowise.server.schemas.runtime_evidence import StatementRow

    cost = StatementCost("src/app.py::save", "s1", 1, 0.25, 500.0, 1000, 2)
    row = action_row(
        cost, workload_name="orders-db", workload_class="production",
        statements=statement_facts(cost),
    ).as_dict()
    parsed = StatementRow.model_validate(row)
    assert parsed.statements.mean_ms == 0.5 and parsed.next_step is None
    assert parsed.model_dump(exclude_unset=True).keys() == row.keys()
    assert row["sources"] == {"share": "statement_stats", "verify": "workload"}
    assert row_compact(row) == {
        "symbol_id": "src/app.py::save", "run_id": "s1", "share": 0.25, "calls": 1000,
        "mean_ms": 0.5,
    }
    assert row_text(row) == [
        "25% of the window's statement time · 1,000 calls · 0.5 ms mean · 2 statements"
        " · tests not mapped",
        "verify: compare this row in the next orders-db window",
    ]


@pytest.mark.asyncio
async def test_production_routes_are_handler_rows_on_the_measured_route(
    ev_client: AsyncClient, session_factory
) -> None:
    """A connected route source and no profile: status says measured, and each
    handler the route join named is a row with its requests; routes on no
    handler are counted, never on a function."""
    from repowise.core.persistence.models import EvidenceRouteLatency

    repo_id = await _repo(ev_client, session_factory)
    async with session_factory() as session:
        session.add(
            EvidenceRun(
                id="q1", repository_id=repo_id, run_key="q1", kind="route_latency",
                format="repowise-evidence-v1", source="plugin", workload_name="api",
                workload_class="production", value_kind="wall", unit="ms", environment="prod",
                commit_sha=HEAD, measured_at=MEASURED_AT,
                window_start=datetime(2026, 11, 1, tzinfo=UTC), window_end=MEASURED_AT,
            )
        )
        await session.flush()
        session.add_all(
            [
                EvidenceRouteLatency(
                    run_id="q1", method="GET", route="/login", repository_id=repo_id,
                    handler_symbol_id=LOGIN, request_count=8, p50_ms=5.0, p95_ms=30.0,
                    total_ms=80.0, error_count=1,
                ),
                EvidenceRouteLatency(
                    run_id="q1", method="GET", route="/elsewhere", repository_id=repo_id,
                    handler_symbol_id=None, request_count=2, p50_ms=None, p95_ms=None,
                    total_ms=20.0, error_count=None,
                ),
            ]
        )
        await session.commit()

    status = (await ev_client.get(f"/api/repos/{repo_id}/evidence/status")).json()
    assert status["status"] == "measured"
    body = (await ev_client.get(f"/api/repos/{repo_id}/evidence/measured")).json()
    assert (body["basis"], body["measured"], body["statements"]) == ("measured", None, None)
    (row,) = body["routes"]["rows"]
    assert (row["symbol_id"], row["share"], row["freshness"]) == (LOGIN, 0.8, None)
    assert row["routes"] == {
        "requests": 8, "total_ms": 80.0, "mean_ms": 10.0, "p95_ms": 30.0, "errors": 1,
        "routes": ["GET /login"],
    }
    assert row["verify"] == "compare this row in the next api window"
    (run,) = body["routes"]["runs"]
    assert (run["requests"], run["routes"]) == (10, 2)
    assert run["unattributed"] == {"unjoined": {"routes": 1, "total_ms": 20.0, "share": 0.2}}


def test_a_route_row_reads_as_requests_in_every_form() -> None:
    """The JSON form validates against the closed ``RouteRow``; the text and
    MCP forms name the route and say requests, mean and p95."""
    from repowise.core.evidence.action_row import action_row, route_facts, row_compact, row_text
    from repowise.core.evidence.measured_fold import RouteCost
    from repowise.server.schemas.runtime_evidence import RouteRow

    cost = RouteCost("src/app.py::serve", "q1", 1, 0.25, 500.0, 1000, 4, 12.5, ("GET /x/{id}",))
    row = action_row(
        cost, workload_name="api", workload_class="production", routes=route_facts(cost)
    ).as_dict()
    parsed = RouteRow.model_validate(row)
    assert parsed.routes.mean_ms == 0.5 and parsed.next_step is None
    assert parsed.model_dump(exclude_unset=True).keys() == row.keys()
    assert row["sources"] == {"share": "route_latency", "verify": "workload"}
    assert row_compact(row) == {
        "symbol_id": "src/app.py::serve", "run_id": "q1", "share": 0.25,
        "route": "GET /x/{id}", "requests": 1000, "mean_ms": 0.5, "p95_ms": 12.5,
    }
    assert row_text(row) == [
        "GET /x/{id} · 25% of the window's request time · 1,000 requests · 0.5 ms mean"
        " · 12.5 ms p95 · 4 errors · tests not mapped",
        "verify: compare this row in the next api window",
    ]
