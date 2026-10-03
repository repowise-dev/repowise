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
from repowise.core.persistence.models import GraphNode, HealthFinding

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


async def _lead_id(get_health) -> str:
    """The top performance opportunity, from the queue the dashboard links to."""
    page = await get_health(include=["performance"], only=["performance_opportunities"], limit=1)
    return page["performance_opportunities"][0]["opportunity_id"]


@pytest.mark.asyncio
async def test_a_bare_dashboard_leads_with_something_to_do(setup_mcp, materialized):
    """Performance carries no defect impact, so it used to lead nowhere.

    Fix first ranks the intervention beside the code-shape work, with the call
    that opens its plan.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health()
    assert "performance_directive" not in result
    perf = [i for i in result["fix_first"]["items"] if i["kind"] == "perf_fix"]
    assert perf, result["fix_first"]
    # The five callers behind one shared helper are one intervention, one item;
    # it points at the loop of its first caller, so its title names the helper.
    shared = [i for i in perf if i["title"].endswith(" load")]
    assert len(shared) == 1, perf
    assert shared[0]["next_call"]["arguments"]["opportunity_id"].startswith("perf")
    assert 0 < len(json.dumps(shared[0])) <= 1500


@pytest.mark.asyncio
async def test_the_cheapest_documented_call_stays_cheap(setup_mcp, materialized):
    """``only=["fix_first"]`` pays for the lead and nothing else."""
    from repowise.server.mcp_server import get_health

    result = await get_health(only=["fix_first"])
    assert set(result) == {"fix_first", "mode", "_meta"}


@pytest.mark.asyncio
async def test_the_summary_rolls_up_and_names_the_next_call(setup_mcp, materialized):
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["performance"], only=["performance_summary"])
    summary = result["performance_summary"]
    assert summary["status"] == "current"
    assert summary["actionability"].get("plan_ready", 0) == 0
    assert summary["with_plan_total"] == 2
    assert summary["analyzed_commit"] == "c" * 40
    assert summary["next_call"]["tool"] == "get_health"
    assert summary["next_call"]["arguments"]["only"] == ["performance_opportunities"]
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
async def test_performance_actionability_threads_through_and_defaults_off_expected(
    setup_mcp, session, materialized
):
    """``performance_actionability`` reaches the service like the other filters,
    and an unfiltered call still excludes ``expected`` (see performance_health's
    default queue)."""
    from repowise.server.mcp_server import get_health

    session.add(
        HealthFinding(
            id=str(uuid.uuid4()),
            repository_id=materialized,
            file_path="src/fs.py",
            biomarker_type="io_in_loop",
            severity="medium",
            function_name="run",
            line_start=1,
            line_end=1,
            details_json=json.dumps(
                {
                    "boundary_kind": "filesystem",
                    "cross_function": True,
                    "path": ["src/fs.py::run", "src/fs.py::read"],
                    "resolution_basis": "reliable-edge",
                }
            ),
            health_impact=0.0,
            reason="A file is read for every loop iteration.",
            dimension="performance",
            status="open",
        )
    )
    await session.flush()
    await finalize_performance_opportunities(session, materialized, analyzed_commit="d" * 40)
    await session.commit()

    default = await get_health(
        include=["performance"], only=["performance_opportunities"]
    )
    assert all(
        item["actionability_state"] != "expected"
        for item in default["performance_opportunities"]
    )

    expected_only = await get_health(
        include=["performance"],
        only=["performance_opportunities"],
        performance_actionability="expected",
    )
    assert expected_only["performance_opportunities_total"] == 1
    assert expected_only["performance_opportunities"][0]["actionability_state"] == "expected"


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
    assert result["ignored_arguments"] == {
        "performance_context": "staging (accepted: production, tooling, test, unknown, all)"
    }


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

    lead = await _lead_id(get_health)
    result = await get_health(opportunity_id=lead)

    assert result["mode"] == "performance_opportunity"
    assert result["found"] is True
    # The lookup flag never shares a name with the lifecycle beside it.
    assert "resolved" not in result
    assert result["opportunity_id"] == lead
    assert result["intervention_symbol"] == "src/shared.py::load"
    assert result["plan_status"] == "available"
    # The plan address space is the refactoring layer's content identity.
    assert result["plan_reference"].startswith("refac4_")
    assert result["confidence"] == "high"
    assert result["fix"]["safety"] == "advisory"
    assert [step["order"] for step in result["plan_steps"]] == [1, 2, 3, 4, 5]
    assert result["validation"]["commands"]
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

    lead = await _lead_id(get_health)
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

    lead = await _lead_id(get_health)
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

    lead = await _lead_id(get_health)
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
            "src/shared.py::load", f"src/shared.py::load_{index}"
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
    assert result["found"] is False
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
async def test_the_lead_links_the_exact_plan_for_the_exact_lead(setup_mcp, materialized):
    """This used to match on a key the plan writer never wrote, so it was null."""
    from repowise.server.mcp_server import get_health

    page = await get_health(include=["performance"], only=["performance_opportunities"], limit=1)
    lead = page["performance_opportunities"][0]
    assert lead["plan_reference"].startswith("refac4_")
    plan = await get_health(plan_id=lead["plan_reference"])
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
        # A distinct helper per row, so these are forty causes rather than one.
        row = _row(materialized, f"src/bulk_{index}.py", 500 + index)
        row.details_json = row.details_json.replace(
            "src/shared.py::load", f"src/shared.py::load_{index}"
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


@pytest.mark.asyncio
async def test_a_plan_is_validated_by_a_test_named_for_its_file_when_no_edge_reaches_it(
    setup_mcp, session, health_data
):
    """The plan selector used to rebuild validation from nothing: always unknown."""
    from repowise.server.mcp_server import get_health

    session.add(GraphNode(repository_id=health_data, node_id="tests/test_a.py", is_test=True))
    for index, caller in enumerate(_CALLERS, start=10):
        session.add(_row(health_data, caller, index))
    await session.flush()
    await finalize_performance_opportunities(session, health_data, analyzed_commit="c" * 40)
    await session.commit()

    opportunity = await get_health(opportunity_id=await _lead_id(get_health))
    assert opportunity["validation"]["via"] == "name-match"
    plan = (await get_health(plan_id=opportunity["plan_reference"]))["plan"]

    assert "tests/test_a.py" in opportunity["validation"]["tests"]
    assert plan["validation"]["via"] == "name-match"
    assert "pytest tests/test_a.py" in plan["validation"]["commands"]
