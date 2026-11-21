"""``get_health(include=["performance"])`` carries what stored runs measured.

With ranked evidence the ``performance_measured`` block lists, for each
workload's newest run, its functions ranked by self time (the cost) with their
basis (workload class, age, samples, where the run's time went, interval);
without it, one ``status`` token says why. Nothing else in any response moves.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.crud import replace_symbol_hotness, replace_workload_hotness
from repowise.core.persistence.models import EvidenceRun
from tests.unit.server.mcp import test_performance_levels

LOGIN = "src/auth/service.py::login"
SHARED = "src/shared.py::load"
MEASURED_AT = datetime(2026, 11, 15, tzinfo=UTC)

#: Five callers behind one shared helper, materialized into the queue.
materialized = test_performance_levels.materialized


#: Run id -> workload class of the runs the tests store.
RUNS = {"r1": "entry_point", "r2": "benchmark", "r3": "unit_test"}


def _hot(symbol_id: str, *, run: str = "r1", rank: int = 1, **over) -> dict:
    """A ``workload_hotness`` row: ``self_share`` is the cost it ranks by."""
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


async def _seed(session, repo_id: str, rows: list[dict]) -> None:
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
            commit_sha="c" * 40,
            measured_at=MEASURED_AT,
            total_value=1000.0,
            explained_value=950.0,
            repo_self_value=470.0,
            wait_value=300.0,
            library_value=120.0,
            unlisted_symbols=4,
        )
        for run_id, cls in RUNS.items()
    )
    await session.flush()
    # ``unranked_reason`` reads the per-function view.
    per_symbol = {r["symbol_id"]: r["freshness"] for r in rows}
    await replace_symbol_hotness(
        session, repo_id, [{"symbol_id": k, "freshness": v} for k, v in per_symbol.items()]
    )
    await replace_workload_hotness(session, repo_id, [r for r in rows if r["tier"] != "measured"])
    await session.commit()


async def _measured(**kw) -> dict:
    from repowise.server.mcp_server import get_health

    return (await get_health(include=["performance"], only=["performance_measured"], **kw))[
        "performance_measured"
    ]


def _size(result: dict) -> int:
    result.get("_meta", {}).pop("timing_ms", None)
    return len(json.dumps(result, default=str))


@pytest.mark.asyncio
async def test_no_runtime_data_is_one_token_and_a_command(setup_mcp, materialized):
    assert await _measured() == {
        "basis": "unknown",
        "status": "no_runtime_data",
        "command": "repowise evidence setup --format json",
    }


@pytest.mark.asyncio
async def test_stale_and_not_ranked(setup_mcp, session, materialized):
    await _seed(session, materialized, [_hot(LOGIN, tier="measured", freshness="stale")])
    assert (await _measured())["status"] == "stale"

    await replace_symbol_hotness(session, materialized, [{"symbol_id": LOGIN, "rank": 14, "freshness": "current"}])
    await session.commit()
    assert await _measured() == {"basis": "unknown", "status": "not_ranked"}


@pytest.mark.asyncio
async def test_rows_carry_their_basis_and_cap(setup_mcp, session, materialized):
    rows = [_hot(LOGIN)]
    rows += [_hot(f"src/app.py::f{i}", rank=i + 2) for i in range(3)]
    rows += [_hot(SHARED, rank=5)]
    # A benchmark ranks the same function: it keeps its own row there.
    rows += [_hot(LOGIN, run="r2")]
    rows += [_hot("src/app.py::t", run="r3")]
    await _seed(session, materialized, rows)

    block = await _measured()
    assert block["basis"] == "measured"
    # Three per run, so the benchmark's row is not cut away by the other run's.
    # Runs measured at the same time come in a stable order (newest, then id).
    assert [(r["run_id"], r["rank"]) for r in block["rows"]] == [
        ("r2", 1), ("r1", 1), ("r1", 2), ("r1", 3),
    ]
    assert (block["rows_total"], block["rows_emitted"], block["rows_reduced_reason"]) == (
        6,
        4,
        "limit",
    )
    row = next(r for r in block["rows"] if r["run_id"] == "r1" and r["rank"] == 1)
    assert row["lines"] == "20-40"
    # ``share`` is the cost (self time) with its interval; inclusive is context.
    assert (row["share"], row["share_low"], row["share_high"], row["samples"]) == (
        0.1,
        0.08,
        0.12,
        100,
    )
    assert row["inclusive"] == 0.3 and "self_share" not in row
    # Unit-test runs are listed apart and never lead.
    assert [r["symbol_id"] for r in block["in_tests"]] == ["src/app.py::t"]
    assert block["in_tests_total"] == 1 and "in_tests_reduced_reason" not in block
    runs = {run["run_id"]: run for run in block["runs"]}
    assert set(runs) == {"r1", "r2", "r3"}
    assert runs["r1"]["workload_class"] == "entry_point"
    assert runs["r1"]["samples"] == 1000
    assert (runs["r1"]["repo_self_share"], runs["r1"]["wait_share"]) == (0.47, 0.3)
    assert "explained_share" not in runs["r1"]
    assert runs["r1"]["age_days"] is not None and runs["r1"]["commit"] == "c" * 12

    wide = await _measured(limit=10)
    assert len(wide["rows"]) == 6 and "rows_reduced_reason" not in wide
    # A function a production static cause sits on keeps its place and names the cause.
    shared = next(r for r in wide["rows"] if r["symbol_id"] == SHARED)
    assert shared["rank"] == 5 and shared["on_static_findings"][0].startswith("perf")
    assert "on_static_findings" not in wide


@pytest.mark.asyncio
async def test_totals_count_past_the_page_cap(setup_mcp, session, materialized):
    """Twelve ranked functions: the page holds ten, the total says twelve."""
    await _seed(session, materialized, [_hot(f"src/app.py::f{i}", rank=i + 1) for i in range(12)])

    block = await _measured(limit=10)
    assert (block["rows_total"], block["rows_emitted"], block["rows_reduced_reason"]) == (
        12,
        10,
        "limit",
    )
    empty = await _measured(limit=0)
    assert (empty["rows"], empty["rows_total"]) == ([], 12)


@pytest.mark.asyncio
async def test_no_surface_word_hot_or_cold(setup_mcp, session, materialized):
    await _seed(session, materialized, [_hot(LOGIN), _hot("src/app.py::t", run="r3")])
    text = json.dumps(await _measured(limit=10))
    assert not re.search(r"\b(hot|cold)", text, re.IGNORECASE), text


@pytest.mark.asyncio
async def test_other_calls_do_not_move(setup_mcp, session, materialized):
    """Evidence reaches only ``include=["performance"]`` on the dashboard."""
    from repowise.server.mcp_server import get_health

    default = _size(await get_health())
    summary = _size(await get_health(include=["performance"], only=["performance_summary"]))
    targeted = await get_health(targets=["src/auth/service.py"], include=["performance"])
    assert "performance_measured" not in targeted

    await _seed(session, materialized, [_hot(LOGIN)])
    plain = await get_health()
    assert "performance_measured" not in plain
    assert _size(plain) == default
    assert _size(await get_health(include=["performance"], only=["performance_summary"])) == summary
    targeted = await get_health(targets=["src/auth/service.py"], include=["performance"])
    assert "performance_measured" not in targeted


@pytest.mark.asyncio
async def test_summary_names_the_queue_call_only_when_the_queue_is_not_here(
    setup_mcp, materialized
):
    from repowise.server.mcp_server import get_health

    full = await get_health(include=["performance"])
    assert "performance_opportunities" in full
    assert "next_call" not in full["performance_summary"]
    alone = await get_health(include=["performance"], only=["performance_summary"])
    assert alone["performance_summary"]["next_call"]["tool"] == "get_health"
    # ``limit=0`` lists no item, so the summary still points at the queue.
    totals_only = await get_health(include=["performance"], limit=0)
    assert totals_only["performance_opportunities"] == []
    assert totals_only["performance_summary"]["next_call"]["tool"] == "get_health"


@pytest.mark.asyncio
async def test_an_index_from_before_the_workload_view_says_to_update(setup_mcp, session, materialized):
    await _seed(session, materialized, [])
    ranked = {"symbol_id": LOGIN, "rank": 1, "tier": "ranked", "freshness": "current"}
    await replace_symbol_hotness(session, materialized, [{**ranked, "run_id": "r1"}])
    await session.commit()
    assert await _measured() == {
        "basis": "unknown", "status": "needs_update", "command": "repowise update",
    }


@pytest.mark.asyncio
async def test_a_thin_newest_run_beside_a_ranked_older_one_is_not_ranked(
    setup_mcp, session, materialized
):
    """The view is built and empty: an update could never change that answer."""
    from datetime import timedelta

    await _seed(session, materialized, [])
    session.add(
        EvidenceRun(
            id="r1-new", repository_id=materialized, run_key="r1-new", kind="profile",
            format="repowise-evidence-v1", source="cli", workload_name="w-r1",
            workload_class="entry_point", measured_at=MEASURED_AT + timedelta(days=1),
        )
    )
    # Ranked in the older run of the workload only.
    ranked = {"symbol_id": LOGIN, "rank": 1, "tier": "ranked", "freshness": "current"}
    await replace_symbol_hotness(session, materialized, [{**ranked, "run_id": "r1"}])
    await session.commit()
    assert await _measured() == {"basis": "unknown", "status": "not_ranked"}


@pytest.mark.asyncio
async def test_a_measured_only_cause_stays_off_the_agent_surface(setup_mcp, session, materialized):
    """A cause minted from a measurement alone is work on the people's
    surfaces; the agent's default response does not change by one byte."""
    from repowise.core.persistence.crud import finalize_performance_opportunities
    from repowise.core.persistence.crud.analysis.fix_first import (
        clear_fix_first_cache,
        load_fix_first,
    )
    from repowise.core.persistence.crud.analysis.performance import measured_hotspot_ids
    from repowise.server.mcp_server import get_health, get_overview

    default = _size(await get_health())
    overview = _size(await get_overview())
    performance = await get_health(include=["performance"], limit=50)

    await _seed(session, materialized, [_hot("src/app.py::decode", tier="lead_candidate")])
    await finalize_performance_opportunities(session, materialized, analyzed_commit="c" * 40)
    await session.commit()
    clear_fix_first_cache()

    (minted,) = await measured_hotspot_ids(session, materialized)
    people = await load_fix_first(session, materialized, limit=None)
    assert minted in {i.source.opportunity_id for i in people.items}

    plain = await get_health()
    assert _size(plain) == default
    assert minted not in json.dumps(plain)
    after = await get_health(include=["performance"], limit=50)
    assert minted not in json.dumps(after)
    # Items, totals, facets and the summary all leave it out: only the
    # existing measured block is new.
    for block in (after, performance):
        block.pop("performance_measured", None)
        block.pop("_meta", None)
    # A rebuild writes its plans anew, so only their ids may differ.
    def plain(block: dict) -> str:
        return re.sub(r'"plan_id": "[0-9a-f]{32}"', '"plan_id": ""', json.dumps(block))

    assert plain(after) == plain(performance)
    assert len(json.dumps(after)) == len(json.dumps(performance))
    shown = await get_overview()
    assert _size(shown) == overview
    assert minted not in json.dumps(shown)
