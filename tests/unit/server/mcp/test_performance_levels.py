"""The agent contract for performance: actionable by default, deep on demand.

Four levels, one read model. The dashboard leads with something to do, the
summary rolls it up, the queue pages it, and one id drills all the way to
evidence and back out through the finding selector. The REST surface is the
same projection of the same rows, which is what these tests hold it to.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from repowise.core.persistence.crud import finalize_performance_opportunities
from repowise.core.persistence.models import HealthFinding

_CALLERS = ("src/a.py", "src/b.py", "src/c.py", "src/d.py", "src/e.py")


def _row(repo_id: str, caller: str, line: int) -> HealthFinding:
    return HealthFinding(
        id=str(uuid.uuid4()),
        repository_id=repo_id,
        file_path=caller,
        biomarker_type="serial_await_in_loop",
        severity="medium",
        function_name="run",
        line_start=line,
        line_end=line,
        details_json=json.dumps(
            {
                "boundary_kind": "db",
                "cross_function": True,
                "path": [f"{caller}::run", "src/shared.py::load", "src/db.py::fetch"],
                "resolution_basis": "call-site",
                "dataflow_verified": True,
            }
        ),
        health_impact=0.0,
        reason="Awaited database work repeats for every loop iteration.",
        dimension="performance",
        status="open",
    )


@pytest.fixture
async def materialized(session, health_data: str) -> str:
    """Five callers behind one shared helper, plus the base fixture's own row.

    The fixture contributes a second production cause, which is why the totals
    below are two rather than one.
    """
    for index, caller in enumerate(_CALLERS, start=10):
        session.add(_row(health_data, caller, index))
    await session.flush()
    await finalize_performance_opportunities(session, health_data, analyzed_commit="c" * 40)
    await session.commit()
    return health_data


class _Statements:
    """Counts statements on every engine, including ones a tool opens itself."""

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self):
        event.listen(Engine, "before_cursor_execute", self._hit)
        return self

    def __exit__(self, *_exc) -> None:
        event.remove(Engine, "before_cursor_execute", self._hit)

    def _hit(self, *_args, **_kwargs) -> None:
        self.n += 1


@pytest.mark.asyncio
async def test_a_bare_dashboard_leads_with_something_to_do(setup_mcp, materialized):
    """Performance carries no defect impact, so it never won the main lead.

    The dashboard reported counts and nothing an agent could act on. The
    additive lead is one primary-key read of the current summary row.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health()
    directive = result["performance_directive"]
    assert directive["status"] == "plan_ready"
    assert directive["opportunity_id"].startswith("perf")
    assert directive["plan_state"] == "available"
    assert directive["next_action"] == {
        "tool": "get_health",
        "arguments": {"opportunity_id": directive["opportunity_id"]},
    }
    assert 0 < len(json.dumps(directive)) <= 1500
    # The existing lead is untouched.
    assert "directive" in result


@pytest.mark.asyncio
async def test_the_cheapest_documented_call_stays_cheap(setup_mcp, materialized):
    """``only=["directive"]`` must not start paying for the second lead."""
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["directive"])
    assert "performance_directive" not in result
    assert "directive" in result


@pytest.mark.asyncio
async def test_a_clear_repository_never_reports_that_it_is_fast(setup_mcp, health_data):
    """No supported pattern is not a measurement of how the code runs."""
    from repowise.core.persistence.crud import finalize_performance_opportunities as finalize
    from repowise.server.mcp_server import _state, get_health

    async with _state._session_factory() as session:
        await session.execute(
            HealthFinding.__table__.delete().where(HealthFinding.dimension == "performance")
        )
        await finalize(session, health_data)
        await session.commit()
    directive = (await get_health())["performance_directive"]
    assert directive["status"] == "clear"
    assert "fast" not in directive["detail"].lower()


@pytest.mark.asyncio
async def test_an_unanalyzed_index_is_unavailable_not_clear(setup_mcp, health_data):
    """A missing materialization must not read as a clean repository."""
    from repowise.server.mcp_server import get_health

    directive = (await get_health())["performance_directive"]
    assert directive["status"] == "unavailable"
    assert directive["reason"] == "no_materialized_analysis"


@pytest.mark.asyncio
async def test_the_summary_rolls_up_and_names_the_next_call(setup_mcp, materialized):
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["performance"], only=["performance_summary"])
    summary = result["performance_summary"]
    assert summary["status"] == "current"
    assert summary["actionability"]["plan_ready"] == 1
    assert summary["with_plan_total"] == 2
    assert summary["analyzed_commit"] == "c" * 40
    assert "get_health" in summary["next_call"]
    assert len(json.dumps(summary)) <= 3000


@pytest.mark.asyncio
async def test_the_queue_filters_before_it_caps(setup_mcp, materialized):
    from repowise.server.mcp_server import get_health

    result = await get_health(
        include=["performance"],
        only=["performance_opportunities"],
        performance_context="production",
        performance_boundary="db",
    )
    assert result["performance_opportunities_total"] == 2
    assert {
        item["execution_context"] for item in result["performance_opportunities"]
    } == {"production"}

    narrowed = await get_health(
        include=["performance"],
        only=["performance_opportunities"],
        performance_confidence="medium",
    )
    assert narrowed["performance_opportunities_total"] == 0

    empty = await get_health(
        include=["performance"],
        only=["performance_opportunities"],
        performance_context="test",
    )
    assert empty["performance_opportunities_total"] == 0


@pytest.mark.asyncio
async def test_an_unrecognized_filter_value_is_named_not_silently_empty(
    setup_mcp, materialized
):
    from repowise.server.mcp_server import get_health

    result = await get_health(
        include=["performance"],
        only=["performance_opportunities"],
        performance_context="staging",
    )
    assert result["performance_opportunities_total"] == 2
    assert result["ignored_arguments"] == {"performance_context": "staging"}


@pytest.mark.asyncio
async def test_the_summary_view_omits_evidence_without_changing_identity(
    setup_mcp, materialized
):
    from repowise.server.mcp_server import get_health

    detailed = await get_health(include=["performance"], only=["performance_opportunities"])
    summary = await get_health(
        include=["performance"],
        only=["performance_opportunities"],
        performance_view="summary",
    )
    assert (
        detailed["performance_opportunities"][0]["opportunity_id"]
        == summary["performance_opportunities"][0]["opportunity_id"]
    )
    assert "facets" not in summary["performance_opportunities"][0]
    assert "facets" in detailed["performance_opportunities"][0]


@pytest.mark.asyncio
async def test_one_id_returns_the_cause_its_plan_and_its_rank_rationale(
    setup_mcp, materialized
):
    from repowise.server.mcp_server import get_health

    lead = (await get_health())["performance_directive"]["opportunity_id"]
    result = await get_health(opportunity_id=lead)

    assert result["mode"] == "performance_opportunity"
    assert result["resolved"] is True
    assert result["opportunity_id"] == lead
    assert result["intervention_symbol"] == "src/shared.py::load"
    assert result["plan_status"] == "available"
    # The plan address space is the refactoring layer's content identity.
    assert result["plan_reference"].startswith("refac2_")
    assert result["confidence"] == "high"
    assert result["fix"]["safety"] == "proven"
    assert result["facets"]["leverage"] == "shared"
    assert result["why_ranked"]
    assert len(json.dumps(result)) <= 20_000

    # The plan reference resolves through the plan selector.
    plan = await get_health(plan_id=result["plan_reference"])
    assert plan["resolved"] is True


@pytest.mark.asyncio
async def test_evidence_pages_to_exhaustion_with_no_duplicate_or_missing_row(
    setup_mcp, materialized
):
    from repowise.server.mcp_server import get_health

    lead = (await get_health())["performance_directive"]["opportunity_id"]
    seen: list[str] = []
    cursor: int | None = 0
    while cursor is not None:
        page = await get_health(
            opportunity_id=lead, only=["performance_evidence"], cursor=cursor, limit=2
        )
        seen.extend(item["finding_id"] for item in page["evidence"])
        cursor = page.get("evidence_next_cursor")
    assert len(seen) == len(set(seen)) == 5


@pytest.mark.asyncio
async def test_every_evidence_reference_round_trips_through_the_finding_selector(
    setup_mcp, materialized
):
    """Evidence used to carry the storage row id, which a reindex replaces."""
    from repowise.server.mcp_server import get_health

    lead = (await get_health())["performance_directive"]["opportunity_id"]
    page = await get_health(opportunity_id=lead, only=["performance_evidence"], limit=50)
    for item in page["evidence"]:
        resolved = await get_health(finding_id=item["finding_id"])
        assert resolved["resolved"] is True
        assert resolved["finding"]["file_path"] == item["file_path"]
        assert resolved["finding"]["id"] == item["finding_id"]


@pytest.mark.asyncio
async def test_asking_for_no_rows_returns_no_rows(setup_mcp, materialized):
    """``limit=0`` is the documented way to ask for the totals and nothing else.

    Reading it as "unset" would hand back a page the caller declined, which is
    the same defect the ranked collections already fixed.
    """
    from repowise.server.mcp_server import get_health

    lead = (await get_health())["performance_directive"]["opportunity_id"]
    page = await get_health(
        opportunity_id=lead, only=["performance_evidence"], limit=0
    )
    assert page["evidence"] == []
    assert page["evidence_total"] == 5
    assert page["evidence_next_cursor"] == 0

    detail = await get_health(opportunity_id=lead, limit=0)
    assert detail["evidence"] == []
    assert detail["evidence_total"] == 5


@pytest.mark.asyncio
async def test_the_summary_costs_the_same_however_many_causes_there_are(
    setup_mcp, materialized, session
):
    """The facet aggregate reads every open cause; its query count must not."""
    from repowise.server.mcp_server import get_health

    with _Statements() as small:
        await get_health(include=["performance"], only=["performance_summary"])

    for index in range(40):
        row = _row(materialized, f"src/bulk_{index}.py", 500 + index)
        row.details_json = row.details_json.replace(
            "src/db.py::fetch", f"src/db.py::fetch_{index}"
        )
        session.add(row)
    await session.flush()
    await finalize_performance_opportunities(session, materialized)
    await session.commit()

    with _Statements() as large:
        result = await get_health(include=["performance"], only=["performance_summary"])
    assert result["performance_summary"]["total"] > 40
    assert large.n == small.n
    assert {entry["value"] for entry in result["performance_summary"]["facets"]["context"]}


@pytest.mark.asyncio
async def test_an_id_from_an_older_model_reports_stale_and_how_to_refresh(
    setup_mcp, materialized
):
    from repowise.server.mcp_server import get_health

    result = await get_health(opportunity_id="perf_0123456789abcdef0123")
    assert result["resolved"] is False
    assert result["model_state"]["state"] == "stale_model"
    assert result["model_state"]["refresh_required"] is True
    assert "repowise update" in result["detail"]

    unknown = await get_health(opportunity_id="not-an-id")
    assert unknown["model_state"]["state"] == "unrecognized"


@pytest.mark.asyncio
async def test_two_detail_selectors_conflict_explicitly(setup_mcp, materialized):
    """Preferring one silently answered confidently about the wrong question."""
    from repowise.server.mcp_server import get_health

    result = await get_health(finding_id="finding_x", opportunity_id="perf2_x")
    assert result["mode"] == "conflict"
    assert result["resolved"] is False
    assert result["selectors"] == ["finding_id", "opportunity_id"]


@pytest.mark.asyncio
async def test_the_lede_links_the_exact_plan_for_the_exact_lead(setup_mcp, materialized):
    """This used to match on a key the plan writer never wrote, so it was null."""
    from repowise.server.mcp_server import get_health

    result = await get_health(
        include=["performance", "refactoring"], only=["recommendation_lede"]
    )
    lede = result["recommendation_lede"]
    assert lede["performance_plan_id"] is not None
    assert lede["performance_plan_id"].startswith("refac2_")
    plan = await get_health(plan_id=lede["performance_plan_id"])
    assert plan["resolved"] is True


@pytest.mark.asyncio
async def test_the_queue_costs_the_same_however_many_opportunities_there_are(
    setup_mcp, materialized, session
):
    """Query count is what this holds: wall clock on a laptop is not evidence."""
    from repowise.server.mcp_server import get_health

    with _Statements() as small:
        await get_health(include=["performance"], only=["performance_opportunities"], limit=2)

    for index in range(40):
        # A distinct sink per row, so these are forty causes rather than one.
        row = _row(materialized, f"src/bulk_{index}.py", 500 + index)
        row.details_json = row.details_json.replace(
            "src/db.py::fetch", f"src/db.py::fetch_{index}"
        )
        session.add(row)
    await session.flush()
    await finalize_performance_opportunities(session, materialized)
    await session.commit()

    with _Statements() as large:
        result = await get_health(
            include=["performance"], only=["performance_opportunities"], limit=2
        )
    assert result["performance_opportunities_total"] > 40
    assert large.n == small.n
    assert len(result["performance_opportunities"]) == 2


@pytest.mark.asyncio
async def test_rest_and_the_agent_surface_project_the_same_rows(
    setup_mcp, materialized, factory
):
    """Two adapters, one service. Order, totals, identity, and plan state agree."""
    from repowise.server.mcp_server import get_health
    from repowise.server.routers.code_health.performance_routes import (
        list_performance_opportunities,
    )

    agent = await get_health(
        include=["performance"], only=["performance_opportunities"], limit=6
    )
    async with factory() as session:
        rest = await list_performance_opportunities(
            materialized,
            context="all",
            boundary=None,
            confidence=None,
            actionability=None,
            view="detail",
            sort="rank",
            file_paths=None,
            limit=6,
            offset=0,
            session=session,
        )

    assert rest["total"] == agent["performance_opportunities_total"]
    shared = (
        "opportunity_id",
        "rank_position",
        "actionability_state",
        "execution_context",
        "boundary_kind",
        "plan_status",
        "confidence",
        "observations_total",
        "affected_call_sites_total",
    )
    assert [{key: item[key] for key in shared} for item in rest["items"]] == [
        {key: item[key] for key in shared} for item in agent["performance_opportunities"]
    ]
    # Two address spaces for the same plan: the row id this surface's own
    # detail route resolves, and the reference the agent selector resolves.
    assert all(item["plan_id"] for item in rest["items"] if item["plan_status"] == "available")
    assert "plan_reference" not in rest["items"][0]
    assert all(
        item["plan_reference"]
        for item in agent["performance_opportunities"]
        if item["plan_status"] == "available"
    )
