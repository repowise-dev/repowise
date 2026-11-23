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
    assert out["file_costs"] == {"basis": "inferred", "files": [], "files_total": 0, "not_production": 0, "every_test": 0}
