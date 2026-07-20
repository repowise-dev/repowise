"""New "By the Numbers" signals: punch card, commit velocity, change-risk mix,
truck factor, dependency cycles, and the wiki build meta.

The pure summarizers are unit-tested directly; the commit-scan signals go
through ``_activity`` on a seeded ``git_commits`` table so the wiring (SELECT
columns, bucketing) is covered end to end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from repowise.core.persistence.crud import get_repository, upsert_git_commits_bulk
from repowise.core.persistence.database import get_session
from repowise.server.routers.stats import (
    _activity,
    _commit_velocity,
    _people,
    _punch_card_summary,
)
from tests.unit.server.conftest import create_test_repo


def _commit(sha: str, dt: datetime, risk: str = "low") -> dict:
    return {
        "sha": sha,
        "author_name": "Jane Doe",
        "author_email": "jane@company.com",
        "committed_at": dt,
        "subject": f"commit {sha}",
        "lines_added": 5,
        "lines_deleted": 1,
        "files_changed": 1,
        "dirs_changed": 1,
        "subsystems_changed": 1,
        "entropy": 0.1,
        "is_fix": False,
        "change_risk_score": 1.0,
        "change_risk_level": risk,
    }


# ---------------------------------------------------------------------------
# Pure summarizers
# ---------------------------------------------------------------------------


def test_punch_card_summary_peak() -> None:
    punch = [[0] * 24 for _ in range(7)]
    punch[2][14] = 10  # Wednesday 2pm — the peak
    punch[0][9] = 4  # Monday 9am
    punch[5][11] = 3  # Saturday (weekend)
    punch[6][12] = 2  # Sunday (weekend)
    dated = 10 + 4 + 3 + 2

    out = _punch_card_summary(punch, dated_total=dated)

    assert out["peak"] == {"weekday": 2, "hour": 14, "count": 10}
    assert out["busiest_weekday"] == 2
    assert out["peak_hour"] == 14
    assert out["total"] == dated


def test_punch_card_summary_empty() -> None:
    out = _punch_card_summary([[0] * 24 for _ in range(7)], dated_total=0)
    assert out["peak"] is None
    assert out["busiest_weekday"] is None
    assert out["total"] == 0


def test_commit_velocity_rising_and_no_prior() -> None:
    anchor = datetime(2026, 6, 1, tzinfo=UTC)
    from datetime import timedelta

    recent = [anchor - timedelta(days=d) for d in (1, 10, 40, 80)]  # 4 in last 90d
    prior = [anchor - timedelta(days=d) for d in (100, 150)]  # 2 in prior 90d
    out = _commit_velocity(recent + prior, anchor)
    assert out["recent_90d"] == 4
    assert out["prior_90d"] == 2
    assert out["pct_change"] == 100.0  # 4 vs 2

    # No prior window → pct_change is None rather than a divide-by-zero spike.
    young = _commit_velocity(recent, anchor)
    assert young["prior_90d"] == 0
    assert young["pct_change"] is None


def test_commit_velocity_empty() -> None:
    assert _commit_velocity([], None) == {"recent_90d": 0, "prior_90d": 0, "pct_change": None}


# ---------------------------------------------------------------------------
# Truck factor (pure logic over _people)
# ---------------------------------------------------------------------------


def _meta(owner: str, path: str) -> SimpleNamespace:
    return SimpleNamespace(primary_owner_name=owner, bus_factor=1, file_path=path)


@pytest.mark.asyncio
async def test_truck_factor_single_dominant_owner() -> None:
    # One owner holds 8/10 files → truck factor 1.
    metas = [_meta("Ada", f"a{i}.py") for i in range(8)] + [
        _meta("Bob", "b.py"),
        _meta("Cara", "c.py"),
    ]
    out = await _people(None, "repo", metas)
    assert out["truck_factor"] == 1


@pytest.mark.asyncio
async def test_truck_factor_spread_ownership() -> None:
    # Four owners with 3/3/2/2 → need 2 to cross 50% of 10.
    metas = (
        [_meta("Ada", f"a{i}.py") for i in range(3)]
        + [_meta("Bob", f"b{i}.py") for i in range(3)]
        + [_meta("Cara", f"c{i}.py") for i in range(2)]
        + [_meta("Dan", f"d{i}.py") for i in range(2)]
    )
    out = await _people(None, "repo", metas)
    assert out["truck_factor"] == 2


@pytest.mark.asyncio
async def test_truck_factor_none_without_owners() -> None:
    out = await _people(None, "repo", [SimpleNamespace(primary_owner_name=None, bus_factor=0, file_path="x.py")])
    assert out["truck_factor"] is None


# ---------------------------------------------------------------------------
# End-to-end through _activity on a seeded commits table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activity_punch_card_and_risk_mix(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    rows = [
        # Wednesday 2024-01-03 at 14:00 UTC — weekday 2, hour 14.
        _commit("w1", datetime(2024, 1, 3, 14, 0, tzinfo=UTC), risk="high"),
        _commit("w2", datetime(2024, 1, 3, 14, 30, tzinfo=UTC), risk="high"),
        # Saturday 2024-01-06 at 11:00 — weekend.
        _commit("s1", datetime(2024, 1, 6, 11, 0, tzinfo=UTC), risk="moderate"),
        # Sunday 2024-01-07 at 12:00 — weekend.
        _commit("s2", datetime(2024, 1, 7, 12, 0, tzinfo=UTC), risk="low"),
    ]
    async with get_session(app.state.session_factory) as session:
        await upsert_git_commits_bulk(session, repo["id"], rows)

    async with get_session(app.state.session_factory) as session:
        repo_row = await get_repository(session, repo["id"])
        activity = await _activity(session, repo["id"], repo_row)

    pc = activity["punch_card"]
    assert pc["matrix"][2][14] == 2  # both Wednesday-2pm commits landed in one cell
    assert pc["peak"] == {"weekday": 2, "hour": 14, "count": 2}
    # Weekend rows carry their commits; which days count as the weekend is a
    # reader preference resolved in the UI, not here.
    assert pc["matrix"][5][11] + pc["matrix"][6][12] == 2

    assert activity["change_risk_mix"] == {"low": 1, "moderate": 1, "high": 2}


@pytest.mark.asyncio
async def test_graph_and_build_degrade_to_zero(client: AsyncClient, app) -> None:
    """The graph + build sections return a fully-shaped zero payload on a repo
    with no graph-membership or LLM-cost rows (index-only / brand-new repo)."""
    from repowise.server.routers.stats import _build, _graph

    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        graph = await _graph(session, repo["id"])
        build = await _build(session, repo["id"])

    assert graph == {
        "cycle_clusters": 0,
        "files_in_cycles": 0,
        "largest_cycle": 0,
        "community_count": 0,
    }
    assert build == {
        "page_count": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "llm_operations": 0,
    }
