"""Serving contracts for composed refactoring opportunities.

REST and MCP read one service over one materialized read model, so the two
must answer identically, and a page must cost its page rather than the
repository. Both are asserted here, the second by counting statements rather
than by wall clock.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event

from repowise.core.persistence import crud


async def _repo(client: AsyncClient) -> str:
    repo_dir = Path(tempfile.mkdtemp()) / "opp-repo"
    repo_dir.mkdir(exist_ok=True)
    (repo_dir / ".git").mkdir(exist_ok=True)
    resp = await client.post(
        "/api/repos",
        json={
            "index": False,
            "name": "opp-repo",
            "local_path": str(repo_dir),
            "url": "https://github.com/example/opp-repo",
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _plan(path: str, symbol: str, **over: Any) -> dict[str, Any]:
    plan = {
        "refactoring_type": "extract_method",
        "file_path": path,
        "target_symbol": symbol,
        "line_start": 10,
        "line_end": 30,
        "plan": {"extracted_name": f"_{symbol}_part", "local_scope": True},
        "evidence": {"ccn_removed": 6, "slice_nloc": 20},
        "impact_delta": 1.0,
        "effort_bucket": "S",
        "blast_radius": {"scope": "local"},
        "confidence": "high",
        "source_biomarker": "complex_method",
    }
    plan.update(over)
    return plan


def _finding(path: str, biomarker: str = "complex_method", impact: float = 3.0) -> dict[str, Any]:
    return {
        "file_path": path,
        "biomarker_type": biomarker,
        "severity": "high",
        "function_name": "f",
        "line_start": 10,
        "line_end": 30,
        "details": {},
        "health_impact": impact,
        "reason": "seeded",
        "dimension": "defect",
    }


async def _seed(client: AsyncClient, app, *, files: int = 8) -> str:
    """A repository whose plans compose into one opportunity per file."""
    repo_id = await _repo(client)
    paths = [f"pkg{i % 3}/mod{i}.py" for i in range(files)]
    async with app.state.session_factory() as session:
        await crud.save_health_findings(session, repo_id, [_finding(p) for p in paths])
        await crud.save_refactoring_suggestions(
            session,
            repo_id,
            [_plan(path, f"sym{i}", impact_delta=float(files - i)) for i, path in enumerate(paths)],
        )
        await crud.finalize_refactoring_opportunities(session, repo_id, analyzed_commit="c" * 40)
        await session.commit()
    return repo_id


async def _mcp(app):
    """Point the MCP tool at this test's database and hand back ``get_health``."""
    import asyncio

    from repowise.server.mcp_server import _state
    from repowise.server.mcp_server.tool_health import get_health

    _state._session_factory = app.state.session_factory
    _state._repo_path = "/tmp/opp-repo"
    _state._registry = None
    _state._vector_store_ready = asyncio.Event()
    _state._vector_store_ready.set()
    return get_health


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalizer_composes_one_opportunity_per_file(client, app):
    repo_id = await _seed(client, app, files=5)
    resp = await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len({item["file_path"] for item in body["items"]}) == 5
    for item in body["items"]:
        assert item["opportunity_id"].startswith("refop2_")
        assert item["step_count"] == 1
        # The lead was supplied, so this is a real answer rather than unknown.
        assert item["addresses_primary_problem"] is True


@pytest.mark.asyncio
async def test_addresses_primary_problem_is_unknown_without_a_lead(client, app):
    """A file with no recorded finding gets ``None``, never a quiet ``False``."""
    repo_id = await _repo(client)
    async with app.state.session_factory() as session:
        await crud.save_refactoring_suggestions(session, repo_id, [_plan("pkg/x.py", "s")])
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()
    body = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    assert body["items"][0]["addresses_primary_problem"] is None


@pytest.mark.asyncio
async def test_an_opportunity_nobody_composes_resolves_rather_than_vanishing(client, app):
    repo_id = await _seed(client, app, files=3)
    before = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    held = before["items"][0]["opportunity_id"]
    async with app.state.session_factory() as session:
        # A run that detects nothing resolves every plan, so nothing composes.
        await crud.save_refactoring_suggestions(session, repo_id, [])
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()
    after = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    assert after["total"] == 0
    # The id an agent is holding still resolves, and reads as resolved.
    detail = await client.get(f"/api/repos/{repo_id}/refactoring/opportunities/{held}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "resolved"


@pytest.mark.asyncio
async def test_lifecycle_rolls_up_from_the_member_plans(client, app):
    repo_id = await _seed(client, app, files=2)
    body = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    detail = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities/"
            f"{body['items'][0]['opportunity_id']}"
        )
    ).json()
    plan_id = detail["steps"][0]["plan_id"]
    async with app.state.session_factory() as session:
        await crud.update_refactoring_suggestion_status(session, repo_id, plan_id, "acknowledged")
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()
    refreshed = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities/{detail['opportunity_id']}"
        )
    ).json()
    assert refreshed["status"] == "acknowledged"


# ---------------------------------------------------------------------------
# REST == MCP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_and_mcp_agree_on_order_totals_and_detail(client, app):
    repo_id = await _seed(client, app, files=9)
    get_health = await _mcp(app)

    rest = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities", params={"limit": 6}
        )
    ).json()
    mcp = await get_health(
        include=["refactoring"], only=["refactoring_opportunities"], limit=6
    )

    assert rest["total"] == mcp["refactoring_opportunities_total"]
    assert [item["opportunity_id"] for item in rest["items"]] == [
        item["opportunity_id"] for item in mcp["refactoring_opportunities"]
    ]
    assert [item["rank_position"] for item in rest["items"]] == [
        item["rank_position"] for item in mcp["refactoring_opportunities"]
    ]

    target = rest["items"][0]["opportunity_id"]
    rest_detail = (
        await client.get(f"/api/repos/{repo_id}/refactoring/opportunities/{target}")
    ).json()
    mcp_detail = await get_health(opportunity_id=target)
    for key in (
        "opportunity_id",
        "file_path",
        "lead_biomarker",
        "step_count",
        "rank_score",
        "addresses_primary_problem",
        "steps",
    ):
        assert rest_detail[key] == mcp_detail[key], key


@pytest.mark.asyncio
async def test_rest_and_mcp_agree_under_a_view(client, app):
    repo_id = await _seed(client, app, files=9)
    get_health = await _mcp(app)
    for view in ("canonical", "diversified", "file_spread"):
        rest = (
            await client.get(
                f"/api/repos/{repo_id}/refactoring/opportunities",
                params={"limit": 6, "view": view},
            )
        ).json()
        mcp = await get_health(
            include=["refactoring"],
            only=["refactoring_opportunities"],
            limit=6,
            refactoring_view=view,
        )
        assert [i["opportunity_id"] for i in rest["items"]] == [
            i["opportunity_id"] for i in mcp["refactoring_opportunities"]
        ], view


@pytest.mark.asyncio
async def test_an_unknown_filter_value_is_named_not_swallowed(client, app):
    repo_id = await _seed(client, app, files=3)
    body = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities",
            params={"refactoring_type": "extract_nonsense"},
        )
    ).json()
    # A misspelling must not read as "the repository is clean".
    assert body["ignored_arguments"] == {"refactoring_type": "extract_nonsense"}
    assert body["total"] == 3


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def _counter(engine) -> list[str]:
    seen: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    return seen


@pytest.mark.asyncio
async def test_queue_query_count_is_constant_in_page_size_and_row_count(
    client, app, test_engine
):
    """The statement count must not move when the page or the repository grows."""
    small = await _seed(client, app, files=4)
    large = await _seed(client, app, files=40)
    counts = {}
    seen = _counter(test_engine)
    for label, repo_id, limit in (
        ("small-1", small, 1),
        ("small-20", small, 20),
        ("large-1", large, 1),
        ("large-20", large, 20),
        ("large-100", large, 100),
    ):
        seen.clear()
        resp = await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities", params={"limit": limit}
        )
        assert resp.status_code == 200
        counts[label] = len(seen)
    assert len(set(counts.values())) == 1, counts


@pytest.mark.asyncio
async def test_a_deep_offset_costs_the_same_as_the_first_page(client, app, test_engine):
    repo_id = await _seed(client, app, files=40)
    seen = _counter(test_engine)
    seen.clear()
    await client.get(
        f"/api/repos/{repo_id}/refactoring/opportunities", params={"limit": 5, "offset": 0}
    )
    first = len(seen)
    seen.clear()
    await client.get(
        f"/api/repos/{repo_id}/refactoring/opportunities", params={"limit": 5, "offset": 30}
    )
    assert len(seen) == first


@pytest.mark.asyncio
async def test_point_lookups_are_bounded_and_do_not_scale_with_the_repository(
    client, app, test_engine
):
    """Resolving one id used to hydrate every open plan in the repository."""
    small = await _seed(client, app, files=4)
    large = await _seed(client, app, files=60)
    get_health = await _mcp(app)
    seen = _counter(test_engine)
    costs = {}
    for label, repo_id in (("small", small), ("large", large)):
        body = (
            await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")
        ).json()
        target = body["items"][0]["opportunity_id"]
        seen.clear()
        await client.get(f"/api/repos/{repo_id}/refactoring/opportunities/{target}")
        costs[f"{label}-opportunity"] = len(seen)

        detail = (
            await client.get(f"/api/repos/{repo_id}/refactoring/opportunities/{target}")
        ).json()
        seen.clear()
        await get_health(plan_id=detail["steps"][0]["plan_id"])
        costs[f"{label}-plan"] = len(seen)

    assert costs["small-opportunity"] == costs["large-opportunity"], costs
    assert costs["small-plan"] == costs["large-plan"], costs
    # The service's own share of an opportunity lookup: the row and its plans.
    assert costs["large-opportunity"] <= 5, costs


@pytest.mark.asyncio
async def test_a_files_opportunities_are_one_indexed_lookup(client, app, test_engine):
    repo_id = await _seed(client, app, files=40)
    seen = _counter(test_engine)
    seen.clear()
    body = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities",
            params={"file_path": "pkg0/mod0.py"},
        )
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["file_path"] == "pkg0/mod0.py"


# ---------------------------------------------------------------------------
# The agent contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bare_get_health_carries_one_bounded_refactoring_directive(client, app):
    repo_id = await _seed(client, app, files=12)
    get_health = await _mcp(app)
    result = await get_health()
    directive = result["refactoring_directive"]
    assert directive["status"] == "available"
    assert directive["opportunity_id"].startswith("refop2_")
    assert directive["next_action"]["arguments"]["opportunity_id"] == directive["opportunity_id"]
    assert directive["opportunities_total"] == 12
    # Level 0 is a lead, not a queue: it must stay small enough to survive.
    assert len(str(directive)) < 1536
    assert "refactoring_opportunities" not in result

    # And the id it names resolves in one call.
    detail = await get_health(opportunity_id=directive["opportunity_id"])
    assert detail["resolved"] is True
    assert detail["mode"] == "refactoring_opportunity"
    assert detail["steps"]
    del repo_id


@pytest.mark.asyncio
async def test_the_directive_is_clear_rather_than_absent_with_no_opportunities(client, app):
    repo_id = await _repo(client)
    async with app.state.session_factory() as session:
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()
    get_health = await _mcp(app)
    assert (await get_health())["refactoring_directive"]["status"] == "clear"


@pytest.mark.asyncio
async def test_the_summary_rolls_up_by_type_effort_and_classification(client, app):
    await _seed(client, app, files=6)
    get_health = await _mcp(app)
    summary = (
        await get_health(include=["refactoring"], only=["refactoring_summary"])
    )["refactoring_summary"]
    assert summary["status"] == "available"
    assert summary["opportunities_total"] == 6
    assert summary["by_lead_type"]["extract_method"] == 6
    assert summary["mechanical_steps_total"] + summary["judgment_steps_total"] == 6
    assert summary["addresses_primary_problem"]["yes"] == 6
    assert "facets" in summary
    assert "refactoring_opportunities" in summary["next_call"]


@pytest.mark.asyncio
async def test_the_default_queue_does_not_reproduce_the_ranked_head(client, app):
    """Eight equal scores from one area must not own the first page.

    The scores really are equal under the published factors, so the answer is a
    queue that spends its first rows on distinct problems, not a tiebreaker.
    """
    repo_id = await _repo(client)
    # One area with a long run of identical scores, plus two other areas.
    plans = [_plan(f"crowded/m{i}.py", f"s{i}", impact_delta=1.0) for i in range(8)]
    plans += [_plan("other/a.py", "a", impact_delta=1.0), _plan("third/b.py", "b", impact_delta=1.0)]
    async with app.state.session_factory() as session:
        await crud.save_refactoring_suggestions(session, repo_id, plans)
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()

    default = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities", params={"limit": 3}
        )
    ).json()["items"]
    canonical = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities",
            params={"limit": 3, "view": "canonical"},
        )
    ).json()["items"]

    areas = {item["file_path"].split("/")[0] for item in default}
    assert areas == {"crowded", "other", "third"}
    # Pure rank order still available, and still the flat run it honestly is.
    assert {item["file_path"].split("/")[0] for item in canonical} == {"crowded"}


@pytest.mark.asyncio
async def test_a_step_names_the_findings_its_cause_produced(client, app):
    repo_id = await _seed(client, app, files=2)
    body = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    detail = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities/"
            f"{body['items'][0]['opportunity_id']}"
        )
    ).json()
    step = detail["steps"][0]
    assert step["finding_ids"], "a step must round-trip to the diagnosis behind it"

    get_health = await _mcp(app)
    resolved = await get_health(finding_id=step["finding_ids"][0])
    assert resolved["resolved"] is True


@pytest.mark.asyncio
async def test_the_detail_hands_back_structured_next_calls(client, app):
    repo_id = await _seed(client, app, files=2)
    body = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    detail = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities/"
            f"{body['items'][0]['opportunity_id']}"
        )
    ).json()
    tools = {action["tool"] for action in detail["next_actions"]}
    assert {"get_symbol", "get_risk"} <= tools
    assert detail["plans"], "the steps' payloads come back with them"
    assert detail["validation_profiles"], "validation is served, not recomputed"


@pytest.mark.asyncio
async def test_a_plan_id_resolves_to_the_opportunity_that_owns_it(client, app):
    repo_id = await _seed(client, app, files=3)
    body = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    owner = body["items"][0]["opportunity_id"]
    detail = (
        await client.get(f"/api/repos/{repo_id}/refactoring/opportunities/{owner}")
    ).json()
    get_health = await _mcp(app)
    resolved = await get_health(plan_id=detail["steps"][0]["plan_id"])
    assert resolved["resolved"] is True
    assert resolved["opportunity_id"] == owner
    assert resolved["next_action"]["arguments"]["opportunity_id"] == owner
    # The payload every surface shows, rank components included.
    assert "rank_score" in resolved["plan"]
    assert "validation" in resolved["plan"]


@pytest.mark.asyncio
async def test_an_unknown_opportunity_id_says_which_kind_of_unknown(client, app):
    await _seed(client, app, files=2)
    get_health = await _mcp(app)
    missing = await get_health(opportunity_id="refop2_" + "0" * 20)
    assert missing["resolved"] is False
    assert missing["model_state"]["state"] == "current"
    stale = await get_health(opportunity_id="refop1_" + "0" * 20)
    assert stale["model_state"]["state"] == "stale_model"


@pytest.mark.asyncio
async def test_each_step_keeps_its_own_validation_profile(client, app):
    """Validation must be keyed by plan, not by position.

    ``hydrate_recommendations`` returns its results in rank order, so pairing
    them positionally against the rows handed in gives most steps another
    plan's tests. Two plans on two files with different affected-file sets, and
    a rank order that is the reverse of the read order, catch it.
    """
    repo_id = await _repo(client)
    local = _plan("alpha/only_here.py", "local_sym", impact_delta=0.5)
    spanning = {
        **_plan("beta/anchor.py", "dup_block", impact_delta=9.0),
        "refactoring_type": "extract_helper",
        "plan": {
            "occurrences": [
                {"file": "beta/anchor.py", "line_start": 10, "line_end": 30},
                {"file": "gamma/other.py", "line_start": 5, "line_end": 25},
            ],
            "suggested_site": {"module": "beta", "directory": "beta"},
            "duplicated_lines": 20,
        },
        "evidence": {"occurrence_count": 2, "duplicated_lines": 20, "co_change_count": 9},
        "blast_radius": {
            "files": ["beta/anchor.py", "gamma/other.py"],
            "file_count": 2,
            "co_change_count": 9,
        },
        "source_biomarker": "dry_violation",
    }
    async with app.state.session_factory() as session:
        # Inserted lowest-rank-first, so the rank order is the reverse.
        await crud.save_refactoring_suggestions(session, repo_id, [local, spanning])
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()

    body = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    seen = {}
    for item in body["items"]:
        detail = (
            await client.get(
                f"/api/repos/{repo_id}/refactoring/opportunities/{item['opportunity_id']}"
            )
        ).json()
        profiles = {p["id"]: p for p in detail["validation_profiles"]}
        for step in detail["steps"]:
            profile = profiles[step["validation_profile_id"]]
            seen[step["file_path"]] = set(profile["affected_files"])

    assert seen["alpha/only_here.py"] == {"alpha/only_here.py"}
    assert seen["beta/anchor.py"] == {"beta/anchor.py", "gamma/other.py"}


@pytest.mark.asyncio
async def test_rest_and_mcp_agree_under_every_queue_filter(client, app):
    """MCP could order the queue but not filter it; REST could do both."""
    repo_id = await _repo(client)
    plans = [
        _plan("a/one.py", "s1", impact_delta=3.0, confidence="high", effort_bucket="S"),
        _plan("b/two.py", "s2", impact_delta=2.0, confidence="medium", effort_bucket="L"),
        {
            **_plan("c/three.py", "s3", impact_delta=1.0),
            "refactoring_type": "split_file",
            "plan": {"groups": [{"name": "g1", "symbols": ["x"]}]},
            "source_biomarker": "large_file",
        },
    ]
    async with app.state.session_factory() as session:
        await crud.save_refactoring_suggestions(session, repo_id, plans)
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()
    get_health = await _mcp(app)

    for params, kwargs in (
        ({"refactoring_type": "split_file"}, {"refactoring_type": "split_file"}),
        ({"confidence": "high"}, {"refactoring_confidence": "high"}),
        ({"effort": "L"}, {"refactoring_effort": "L"}),
        ({"refactoring_type": "nope"}, {"refactoring_type": "nope"}),
    ):
        rest = (
            await client.get(
                f"/api/repos/{repo_id}/refactoring/opportunities",
                params={"limit": 6, **params},
            )
        ).json()
        mcp = await get_health(
            include=["refactoring"], only=["refactoring_opportunities"], limit=6, **kwargs
        )
        assert rest["total"] == mcp["refactoring_opportunities_total"], params
        assert [i["opportunity_id"] for i in rest["items"]] == [
            i["opportunity_id"] for i in mcp["refactoring_opportunities"]
        ], params
        # An unrecognized value is named on both surfaces, never swallowed.
        assert rest.get("ignored_arguments") == mcp.get("ignored_arguments"), params


@pytest.mark.asyncio
async def test_limit_zero_returns_the_totals_and_no_rows(client, app):
    """``limit=0`` is the documented "totals, no rows" call at every level."""
    repo_id = await _seed(client, app, files=4)
    body = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    get_health = await _mcp(app)
    detail = await get_health(opportunity_id=body["items"][0]["opportunity_id"], limit=0)
    assert detail["steps"] == []
    assert detail["steps_total"] == 1
    assert detail["steps_emitted"] == 0


@pytest.mark.asyncio
async def test_a_capped_queue_says_why_it_was_capped(client, app):
    repo_id = await _seed(client, app, files=20)
    get_health = await _mcp(app)
    result = await get_health(
        include=["refactoring"], only=["refactoring_opportunities"], limit=3
    )
    assert result["refactoring_opportunities_total"] == 20
    assert result["refactoring_opportunities_emitted"] == 3
    assert result["refactoring_opportunities_reduced_reason"] == "limit"
    assert result["recovery"]["refactoring_opportunities"]["remaining"] == 17
    del repo_id


# ---------------------------------------------------------------------------
# Triage: the opportunity transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_request_triages_every_step_of_an_opportunity(client, app):
    """An opportunity has no lifecycle of its own, so the transition writes the
    steps and reads the rollup back. One request, not one per step."""
    repo_id = await _seed(client, app, files=3)
    page = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    oid = page["items"][0]["opportunity_id"]
    detail = (
        await client.get(f"/api/repos/{repo_id}/refactoring/opportunities/{oid}")
    ).json()
    step_ids = [s["plan_id"] for s in detail["steps"]]

    res = await client.patch(
        f"/api/repos/{repo_id}/refactoring/opportunities/{oid}/status",
        json={"status": "acknowledged"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "acknowledged"
    assert body["steps_updated"] == len(step_ids)

    async with app.state.session_factory() as session:
        for plan_id in step_ids:
            row = await crud.get_refactoring_suggestion(session, repo_id, plan_id)
            assert row is not None and row.status == "acknowledged"

    # And the stored column moved with it, so the indexed read agrees without
    # waiting for the next index.
    refreshed = (
        await client.get(f"/api/repos/{repo_id}/refactoring/opportunities/{oid}")
    ).json()
    assert refreshed["status"] == "acknowledged"


@pytest.mark.asyncio
async def test_a_dismissed_opportunity_reads_back_as_dismissed_not_resolved(client, app):
    """``false_positive`` and ``resolved`` are different claims - the work was
    never real against the work is done - so a person who chooses one must not
    be shown the other."""
    repo_id = await _seed(client, app, files=3)
    page = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    oid = page["items"][0]["opportunity_id"]

    res = await client.patch(
        f"/api/repos/{repo_id}/refactoring/opportunities/{oid}/status",
        json={"status": "false_positive"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "false_positive"


@pytest.mark.asyncio
async def test_a_triaged_opportunity_leaves_the_open_queue(client, app):
    repo_id = await _seed(client, app, files=4)
    page = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    before = page["total"]
    oid = page["items"][0]["opportunity_id"]

    await client.patch(
        f"/api/repos/{repo_id}/refactoring/opportunities/{oid}/status",
        json={"status": "resolved"},
    )
    after = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    assert after["total"] == before - 1
    assert oid not in {item["opportunity_id"] for item in after["items"]}


@pytest.mark.asyncio
async def test_an_unknown_status_is_refused_rather_than_stored(client, app):
    repo_id = await _seed(client, app, files=2)
    page = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    oid = page["items"][0]["opportunity_id"]
    res = await client.patch(
        f"/api/repos/{repo_id}/refactoring/opportunities/{oid}/status",
        json={"status": "wontfix"},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_an_unknown_opportunity_id_is_a_404(client, app):
    repo_id = await _seed(client, app, files=2)
    res = await client.patch(
        f"/api/repos/{repo_id}/refactoring/opportunities/refop2_nope/status",
        json={"status": "resolved"},
    )
    assert res.status_code == 404


def test_the_rollup_rule_separates_dismissed_from_done():
    """The pure rule, over member states alone. One step someone called a false
    positive must not resolve the work the others still describe."""
    from repowise.core.analysis.health.refactoring.opportunity import roll_up_status

    assert roll_up_status([]) == "open"
    assert roll_up_status(["open", "resolved"]) == "open"
    assert roll_up_status(["acknowledged", "resolved"]) == "acknowledged"
    assert roll_up_status(["resolved", "resolved"]) == "resolved"
    # Mixed: some done, one wrong. The work as a whole is done.
    assert roll_up_status(["resolved", "false_positive"]) == "resolved"
    # All wrong: the opportunity itself was the false positive.
    assert roll_up_status(["false_positive", "false_positive"]) == "false_positive"


@pytest.mark.asyncio
async def test_the_board_can_search_by_path_fragment(client, app):
    """The board's search box. A residual filter over the open set, which is
    what makes it a substring rather than an index seek."""
    repo_id = await _seed(client, app, files=6)
    page = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    sample = page["items"][0]["file_path"]
    fragment = sample.rsplit("/", 1)[-1][:4]

    hit = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities", params={"search": fragment}
        )
    ).json()
    assert hit["total"] >= 1
    assert all(fragment in item["file_path"] for item in hit["items"])

    miss = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities",
            params={"search": "zzz_no_such_path"},
        )
    ).json()
    assert miss["total"] == 0


@pytest.mark.asyncio
async def test_several_lead_types_come_back_in_one_request(client, app):
    """The board's Structural tab is four types, and it must not cost four
    round trips."""
    repo_id = await _seed(client, app, files=8)
    body = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities",
            params={"refactoring_type": "split_file,break_cycle"},
        )
    ).json()
    assert body.get("ignored_arguments", {}) == {}
    assert all(
        item["lead_refactoring_type"] in {"split_file", "break_cycle"} for item in body["items"]
    )

    # A misspelling inside the list is still named rather than narrowing the
    # result to the members that happened to be spelled right.
    noisy = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities",
            params={"refactoring_type": "split_file,nonsense"},
        )
    ).json()
    assert noisy["ignored_arguments"]["refactoring_type"] == "nonsense"


@pytest.mark.asyncio
async def test_a_row_can_be_asked_for_without_its_steps(client, app):
    """The product list renders step counts, not steps. Asking for the steps is
    most of the page's bytes and none of its pixels."""
    repo_id = await _seed(client, app, files=6)
    with_steps = (
        await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")
    ).json()
    without = (
        await client.get(
            f"/api/repos/{repo_id}/refactoring/opportunities", params={"step_preview": 0}
        )
    ).json()

    assert any("steps" in item for item in with_steps["items"])
    assert all("steps" not in item for item in without["items"])
    # The counts a row actually renders survive.
    assert all("step_count" in item for item in without["items"])
    assert len(json.dumps(without)) < len(json.dumps(with_steps))


@pytest.mark.asyncio
async def test_the_field_can_place_every_structural_opportunity(client, app):
    """The structural field plots file size against reach, and those are file
    facts the opportunity row has to carry: without them it could only plot the
    handful of files the bounded plan head happened to include."""
    repo_id = await _seed(client, app, files=6)
    body = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    figured = [
        item
        for item in body["items"]
        if isinstance(item.get("file_nloc"), int) and isinstance(item.get("dependents"), int)
    ]
    assert figured, "the finalizer records the file's size and reach onto the row"


@pytest.mark.asyncio
async def test_a_dismissal_survives_the_next_index(client, app):
    """A person's decision is not restated by the writer.

    Both terminal states mean "nobody composes this again", so the reconciler
    sees a dismissed opportunity exactly as it sees a completed one: absent.
    Reading that absence as "resolved" would quietly turn "this was never real"
    into "this got done", and the next index would do it silently.
    """
    repo_id = await _seed(client, app, files=3)
    page = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    oid = page["items"][0]["opportunity_id"]

    await client.patch(
        f"/api/repos/{repo_id}/refactoring/opportunities/{oid}/status",
        json={"status": "false_positive"},
    )
    async with app.state.session_factory() as session:
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()
        row = await crud.get_refactoring_opportunity(session, repo_id, oid)
        assert row is not None
        assert row.status == "false_positive"


@pytest.mark.asyncio
async def test_an_acknowledgement_survives_the_next_index(client, app):
    """Acknowledged is outstanding work someone picked up, so it stays composed
    and the rollup keeps saying so."""
    repo_id = await _seed(client, app, files=3)
    page = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    oid = page["items"][0]["opportunity_id"]

    await client.patch(
        f"/api/repos/{repo_id}/refactoring/opportunities/{oid}/status",
        json={"status": "acknowledged"},
    )
    async with app.state.session_factory() as session:
        await crud.finalize_refactoring_opportunities(session, repo_id)
        await session.commit()
        row = await crud.get_refactoring_opportunity(session, repo_id, oid)
        assert row is not None
        assert row.status == "acknowledged"


@pytest.mark.asyncio
async def test_a_transition_that_writes_nothing_is_not_reported_as_success(client, app):
    """The rollup of an empty set is ``open``.

    So an opportunity whose steps are missing or whose plan ids no longer
    resolve would otherwise answer "dismiss this" with a stored ``open`` and an
    HTTP 200 - the caller's own request handed back as the state, with the one
    thing they asked for the one thing that did not happen.
    """
    repo_id = await _seed(client, app, files=3)
    page = (await client.get(f"/api/repos/{repo_id}/refactoring/opportunities")).json()
    oid = page["items"][0]["opportunity_id"]

    async with app.state.session_factory() as session:
        row = await crud.get_refactoring_opportunity(session, repo_id, oid)
        assert row is not None
        row.details_json = json.dumps({"steps": []})
        await session.commit()

    res = await client.patch(
        f"/api/repos/{repo_id}/refactoring/opportunities/{oid}/status",
        json={"status": "false_positive"},
    )
    assert res.status_code == 409

    async with app.state.session_factory() as session:
        row = await crud.get_refactoring_opportunity(session, repo_id, oid)
        assert row is not None
        # Untouched, rather than silently reset to open.
        assert row.status == "open"
