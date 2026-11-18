"""``get_health(include=["performance"])`` carries what stored runs measured.

With ranked evidence the ``performance_measured`` block lists functions with
their basis (workload class, age, samples, explained share and interval);
without it, one ``status`` token says why. Nothing else in any response moves.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.crud import replace_symbol_hotness
from repowise.core.persistence.models import EvidenceRun
from tests.unit.server.mcp import test_performance_levels

LOGIN = "src/auth/service.py::login"
SHARED = "src/shared.py::load"
MEASURED_AT = datetime(2026, 11, 15, tzinfo=UTC)

#: Five callers behind one shared helper, materialized into the queue.
materialized = test_performance_levels.materialized


def _hot(symbol_id: str, *, cls: str = "entry_point", run: str = "r1", **over) -> dict:
    return {
        "symbol_id": symbol_id,
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


async def _seed(session, repo_id: str, rows: list[dict]) -> None:
    session.add(
        EvidenceRun(
            repository_id=repo_id,
            run_key="k1",
            kind="profile",
            format="repowise-evidence-v1",
            source="cli",
            workload_name="w-r1",
            workload_class="entry_point",
        )
    )
    await session.flush()
    await replace_symbol_hotness(session, repo_id, rows)
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
    await _seed(session, materialized, [_hot(LOGIN, tier="measured", freshness="stale", rank=None)])
    assert (await _measured())["status"] == "stale"

    await replace_symbol_hotness(session, materialized, [_hot(LOGIN, tier="measured", rank=14)])
    await session.commit()
    assert await _measured() == {"basis": "unknown", "status": "not_ranked"}


@pytest.mark.asyncio
async def test_rows_carry_their_basis_and_cap(setup_mcp, session, materialized):
    rows = [_hot(LOGIN)]
    rows += [_hot(f"src/app.py::f{i}", rank=i + 2) for i in range(4)]
    rows += [_hot("src/app.py::t", cls="unit_test", run="r3")]
    rows += [_hot(SHARED, rank=9)]
    await _seed(session, materialized, rows)

    block = await _measured()
    assert block["basis"] == "measured"
    assert [r["symbol_id"] for r in block["rows"]][:1] == [LOGIN]
    assert len(block["rows"]) == 3
    assert (block["rows_total"], block["rows_emitted"], block["rows_reduced_reason"]) == (
        5,
        3,
        "limit",
    )
    assert block["rows"][0]["lines"] == "20-40"
    row = block["rows"][0]
    assert (row["share"], row["share_low"], row["share_high"], row["samples"]) == (
        0.3,
        0.27,
        0.33,
        300,
    )
    # Unit-test runs are listed apart and never lead.
    assert [r["symbol_id"] for r in block["in_tests"]] == ["src/app.py::t"]
    assert block["in_tests_total"] == 1 and "in_tests_reduced_reason" not in block
    runs = {run["run_id"]: run for run in block["runs"]}
    assert set(runs) == {"r1", "r3"}
    assert runs["r1"]["workload_class"] == "entry_point"
    assert runs["r1"]["samples"] == 1000 and runs["r1"]["explained_share"] == 0.11
    assert runs["r1"]["age_days"] is not None and runs["r1"]["commit"] == "c" * 40
    # A function a static cause already sits on links to that cause instead.
    (link,) = block["on_static_findings"]
    assert link["symbol_id"] == SHARED and link["opportunity_ids"][0].startswith("perf")
    assert SHARED not in {r["symbol_id"] for r in block["rows"]}

    wide = await _measured(limit=10)
    assert len(wide["rows"]) == 5 and "rows_reduced_reason" not in wide


@pytest.mark.asyncio
async def test_no_surface_word_hot_or_cold(setup_mcp, session, materialized):
    await _seed(session, materialized, [_hot(LOGIN), _hot("src/app.py::t", cls="unit_test")])
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
