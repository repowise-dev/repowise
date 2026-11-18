"""``crud.measured_costs``: what each workload's newest run measured, beside the static causes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

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
