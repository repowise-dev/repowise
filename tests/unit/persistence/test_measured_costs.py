"""``crud.measured_costs``: measured functions beside the static performance causes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import insert

from repowise.core.analysis.function_test_map import MappedTest, encode_tests
from repowise.core.persistence.crud import measured_costs, replace_symbol_hotness
from repowise.core.persistence.models import (
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
MEASURED_AT = datetime(2026, 11, 15, tzinfo=UTC)


def _hot(name: str, *, cls: str = "entry_point", run: str = "r1", **over) -> dict:
    return {
        "symbol_id": f"{APP}::{name}",
        "tier": "ranked",
        "freshness": "current",
        "rank": 1,
        "run_id": run,
        "workload_class": cls,
        "workload_name": f"w-{run}",
        "evidence_commit_sha": "c" * 40,
        "measured_at": MEASURED_AT,
        "total_share": 0.3,
        "self_share": 0.1,
        "share_low": 0.27,
        "share_high": 0.33,
        "samples": 300,
        "run_samples": 1000,
        "explained_share": 0.11,
        "run_unlisted": 4,
        "value_kind": "wall",
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
        "old": (90, 95),
        "weak": (100, 110),
    }
    session.add_all(
        make_wiki_symbol(repo_id, APP, name, start_line=lo, end_line=hi)
        for name, (lo, hi) in spans.items()
    )
    serve = f"{APP}::serve"
    session.add_all(
        [
            GraphNode(repository_id=repo_id, node_id=serve, node_type="symbol"),
            GraphNode(repository_id=repo_id, node_id="src/main.py::main", node_type="symbol"),
            GraphEdge(
                repository_id=repo_id,
                source_node_id="src/main.py::main",
                target_node_id=serve,
                edge_type="calls",
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


async def test_no_evidence_means_no_view(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    assert await measured_costs(async_session, repo.id, now=NOW) is None


async def test_rows_groups_dedupe_and_context(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add_all(
        [
            # On ``load`` itself: that cause holds the measurement, no second row.
            _finding(repo.id, 35, "perf3_load"),
            # Inside ``inner``, nested in ``serve``: not on ``serve``.
            _finding(repo.id, 6, "perf3_inner"),
            # A resolved finding on ``thin`` does not count.
            HealthFinding(
                repository_id=repo.id, file_path=APP, biomarker_type="io_in_loop",
                severity="medium", line_start=75, dimension="performance",
                status="resolved", details_json="{}",
            ),
            # A cause whose edit site is ``helper``.
            PerformanceOpportunity(
                repository_id=repo.id, opportunity_id="perf3_helper", status="open",
                intervention_symbol=f"{APP}::helper", file_path=APP,
                details_json=json.dumps({}),
            ),
        ]
    )
    await async_session.flush()
    await replace_symbol_hotness(
        async_session,
        repo.id,
        [
            _hot("serve"),
            _hot("load", tier="lead_candidate", rank=2),
            _hot("helper", cls="benchmark", run="r2"),
            _hot("thin", cls="unit_test", run="r3", rank=3),
            # Stale evidence and a row below the rank floor are never listed.
            _hot("old", tier="measured", freshness="stale", rank=None),
            _hot("weak", tier="measured", rank=14),
        ],
    )

    view = await measured_costs(async_session, repo.id, now=NOW)

    assert set(view) == {"measured", "measured_in_tests", "on_static_findings"}
    measured = view["measured"]
    assert [r["symbol_id"] for r in measured["rows"]] == [f"{APP}::serve"]
    serve = measured["rows"][0]
    assert serve["lines"] == [1, 20]
    assert serve["callers_total"] == 1 and serve["callers"] == ["src/main.py::main"]
    assert serve["owner"] == {"name": "Ada", "commit_pct": 0.8}
    assert serve["tests"] == ["tests/test_app.py::test_serve"]
    assert (serve["share"], serve["share_low"], serve["share_high"]) == (0.3, 0.27, 0.33)
    assert measured["runs"] == [
        {
            "run_id": "r1",
            "workload_class": "entry_point",
            "workload_name": "w-r1",
            "commit": "c" * 40,
            "measured_at": MEASURED_AT.isoformat(),
            "age_days": 3,
            "samples": 1000,
            "explained_share": 0.11,
            "outside_production": 4,
            "value_kind": "wall",
            "ordering_only": False,
        }
    ]

    tests_group = view["measured_in_tests"]
    assert [r["symbol_id"] for r in tests_group["rows"]] == [f"{APP}::thin"]
    assert [r["workload_class"] for r in tests_group["runs"]] == ["unit_test"]
    # Never mapped to tests: unknown, not "no tests".
    assert tests_group["rows"][0]["tests"] is None

    linked = {r["symbol_id"]: r["opportunity_ids"] for r in view["on_static_findings"]}
    assert linked == {f"{APP}::load": ["perf3_load"], f"{APP}::helper": ["perf3_helper"]}


async def test_unit_test_evidence_never_lists_under_measured(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    await replace_symbol_hotness(
        async_session, repo.id, [_hot("thin", cls="unit_test", tier="lead_candidate")]
    )
    view = await measured_costs(async_session, repo.id, now=NOW)
    assert set(view) == {"measured_in_tests"}


async def test_a_class_that_may_not_lead_and_is_not_a_test_is_not_listed(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    # A finding with no line names no function, so ``serve`` keeps its row.
    async_session.add(_finding(repo.id, 0, "perf3_file"))
    await async_session.flush()
    await replace_symbol_hotness(
        async_session, repo.id, [_hot("serve"), _hot("load", cls="smoke", run="r9", rank=2)]
    )
    view = await measured_costs(async_session, repo.id, now=NOW)
    assert set(view) == {"measured"}
    assert [r["symbol_id"] for r in view["measured"]["rows"]] == [f"{APP}::serve"]


async def test_a_finding_with_no_cause_id_keeps_the_function_listed(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    # No cause carries these findings, so nothing would show the measurement.
    async_session.add_all([_finding(repo.id, 10, None), _finding(repo.id, 75, None)])
    await async_session.flush()
    await replace_symbol_hotness(
        async_session, repo.id, [_hot("serve"), _hot("thin", cls="unit_test", run="r3")]
    )
    view = await measured_costs(async_session, repo.id, now=NOW)
    assert set(view) == {"measured", "measured_in_tests"}
    assert [r["symbol_id"] for r in view["measured"]["rows"]] == [f"{APP}::serve"]
    assert [r["symbol_id"] for r in view["measured_in_tests"]["rows"]] == [f"{APP}::thin"]


async def test_only_stale_or_unranked_rows_mean_no_view(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    await replace_symbol_hotness(
        async_session,
        repo.id,
        [
            _hot("old", tier="ranked", freshness="stale"),
            _hot("weak", tier="measured"),
        ],
    )
    assert await measured_costs(async_session, repo.id, now=NOW) is None


async def test_limit_counts_rows_after_the_dedupe(async_session) -> None:
    repo = await insert_repo(async_session)
    await _seed_code(async_session, repo.id)
    async_session.add(_finding(repo.id, 10, "perf3_serve"))
    await async_session.flush()
    await replace_symbol_hotness(
        async_session,
        repo.id,
        [_hot("serve", tier="lead_candidate"), _hot("load", rank=2), _hot("helper", rank=3)],
    )
    view = await measured_costs(async_session, repo.id, limit=1, now=NOW)
    assert [r["symbol_id"] for r in view["measured"]["rows"]] == [f"{APP}::load"]
    # The total counts the group before the cut: ``load`` and ``helper``.
    assert view["measured"]["total"] == 2
