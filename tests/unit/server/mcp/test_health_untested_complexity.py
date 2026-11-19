"""``get_health(include=["untested_complexity"])``: functions ranked by CRAP.

Measured rows carry a percentage and estimated rows a tier, never both, and an
estimate is listed only when the call also opts into unverified rows.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.persistence.models import HealthFinding


def _row(repo_id: str, name: str, basis: str, crap: float) -> HealthFinding:
    cov = {"coverage_pct": 10.0} if basis == "measured" else {"coverage_tier": "reached"}
    return HealthFinding(
        repository_id=repo_id,
        file_path="src/auth/service.py",
        biomarker_type="untested_complexity",
        severity="high" if crap >= 50 else "medium",
        function_name=name,
        line_start=10,
        line_end=40,
        details_json=json.dumps(
            {
                "crap": crap,
                "ccn": 8,
                "coverage_basis": basis,
                "action": "add_tests",
                **cov,
                "actions": [{"kind": "add-tests", "prompt": f"Test {name}."}],
            }
        ),
        reason=f"{name} has CRAP {crap:.0f}",
        dimension="advisory",
    )


@pytest.fixture
async def crap_rows(session, repo_id):
    session.add_all(
        [
            _row(repo_id, "login", "measured", 40.0),
            _row(repo_id, "refresh", "measured", 80.0),
            _row(repo_id, "guess", "estimated", 60.0),
        ]
    )
    await session.commit()
    return repo_id


@pytest.mark.asyncio
async def test_the_list_is_absent_until_asked_for(setup_mcp, crap_rows):
    from repowise.server.mcp_server import get_health

    result = await get_health()
    assert "untested_complexity" not in result
    assert "untested_complexity" not in (result.get("unknown_include_keys") or [])


@pytest.mark.asyncio
async def test_measured_rows_rank_by_crap_with_a_percentage(setup_mcp, crap_rows):
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["untested_complexity"])
    rows = result["untested_complexity"]
    assert [r["function_name"] for r in rows] == ["refresh", "login"]
    assert all(r["coverage_basis"] == "measured" and "coverage_tier" not in r for r in rows)
    assert rows[0]["coverage_pct"] == 10.0
    assert result["untested_complexity_total"] == 2


@pytest.mark.asyncio
async def test_estimates_join_only_when_opted_in_and_never_as_a_percentage(setup_mcp, crap_rows):
    from repowise.server.mcp_server import get_health

    rows = (await get_health(include=["untested_complexity", "unverified"]))["untested_complexity"]
    assert [r["function_name"] for r in rows] == ["refresh", "guess", "login"]
    guess = rows[1]
    assert guess["coverage_tier"] == "reached"
    assert "coverage_pct" not in guess


@pytest.mark.asyncio
async def test_the_advisory_findings_list_holds_estimates(setup_mcp, crap_rows):
    from repowise.server.mcp_server import get_health

    result = await get_health(targets=["src/auth/service.py"], include=["advisory"])
    names = {
        f.get("function_name")
        for f in result.get("findings", [])
        if f["biomarker_type"] == "untested_complexity"
    }
    assert names == {"login", "refresh"}


@pytest.mark.asyncio
async def test_held_estimates_are_counted_not_listed(setup_mcp, crap_rows):
    from repowise.server.mcp_server import get_health

    gated = (await get_health(targets=["src/auth/service.py"], include=["advisory"]))["_meta"]["gated"]
    assert gated["health"]["estimated"] == 1
    assert gated["opt_in"] == {"include": ["unverified"]}


@pytest.mark.asyncio
async def test_actions_ride_only_on_the_single_finding_view(setup_mcp, crap_rows):
    from repowise.server.mcp_server import get_health

    listed = await get_health(targets=["src/auth/service.py"], include=["advisory"])
    rows = [f for f in listed["findings"] if f["biomarker_type"] == "untested_complexity"]
    assert rows and all("actions" not in f["details"] for f in rows)

    detail = await get_health(finding_id=rows[0]["id"])
    assert detail["finding"]["details"]["actions"][0]["kind"] == "add-tests"
