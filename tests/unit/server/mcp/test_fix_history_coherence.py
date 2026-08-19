"""get_risk and get_context rank and report on the same defect signal.

get_risk already classified a target ``bug-prone`` from counted fixes while the
attention list beside it ranked purely on churn, and get_context's triage card
carried the churn bit alone. Both now read the fix columns that were already on
the row, so the two halves of a response agree about what deserves attention.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import GitMetadata


async def _add_bug_magnet(session, repo_id: str, path: str, *, fixes: int, days: int):
    """A file that is bug-fixed but NOT a churn hotspot.

    This is the population the old is_hotspot-only filter could never surface,
    which is the point of the test: no amount of re-sorting inside the churn set
    would reach it.

    Uses the fixture session rather than opening its own: the fixtures share one
    StaticPool connection and commit at teardown, so a second session that
    commits mid-test drops the seeded data out from under the tool call.
    """
    session.add(
        GitMetadata(
            id=f"gm-magnet-{path}",
            repository_id=repo_id,
            file_path=path,
            commit_count_total=6,
            commit_count_90d=1,
            commit_count_30d=0,
            churn_percentile=0.05,
            is_hotspot=False,
            bug_magnet=True,
            fix_mass=9.0,
            prior_defect_count=fixes,
            last_fix_at=datetime.now(UTC) - timedelta(days=days),
            primary_owner_name="Carol",
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_global_hotspots_surfaces_a_bug_magnet_that_is_not_a_churn_hotspot(
    setup_mcp, session, repo_id
):
    from repowise.server.mcp_server import get_risk

    await _add_bug_magnet(session, repo_id, "src/quiet/breaks_a_lot.py", fixes=6, days=10)

    result = await get_risk(["src/auth/service.py"])
    listed = {h["file_path"]: h for h in result["global_hotspots"]}

    assert "src/quiet/breaks_a_lot.py" in listed
    entry = listed["src/quiet/breaks_a_lot.py"]
    assert entry["fix_count"] == 6
    assert entry["last_fix_days_ago"] == 10
    assert entry["bug_magnet"] is True


@pytest.mark.asyncio
async def test_global_hotspots_ranks_fix_history_ahead_of_churn(setup_mcp, session, repo_id):
    from repowise.server.mcp_server import get_risk

    await _add_bug_magnet(session, repo_id, "src/quiet/breaks_a_lot.py", fixes=6, days=10)

    # models.py is in the fixture with no fix history; target something else so
    # both candidates are eligible for the list.
    result = await get_risk(["src/auth/middleware.py"])
    paths = [h["file_path"] for h in result["global_hotspots"]]

    assert paths, "expected an attention list"
    assert paths[0] == "src/quiet/breaks_a_lot.py"


@pytest.mark.asyncio
async def test_global_hotspots_stays_silent_on_files_without_fix_history(setup_mcp):
    """A repo with no fix data pays nothing: no keys, not zeroed keys."""
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/middleware.py"])
    for entry in result["global_hotspots"]:
        assert "fix_count" not in entry
        assert "bug_magnet" not in entry
        assert "hotspot_score" in entry  # churn ranking still intact


@pytest.mark.asyncio
async def test_risk_summary_leads_with_fix_history_when_present(setup_mcp, session, repo_id):
    from repowise.server.mcp_server import get_risk

    await _add_bug_magnet(session, repo_id, "src/quiet/breaks_a_lot.py", fixes=4, days=7)

    result = await get_risk(["src/quiet/breaks_a_lot.py"])
    summary = result["targets"]["src/quiet/breaks_a_lot.py"]["risk_summary"]

    # The defect evidence comes before the churn number, and agrees with the
    # risk_type printed further along the same line.
    assert "4 bug fixes in 6mo, last 7d ago (bug magnet)" in summary
    assert summary.index("bug fixes") < summary.index("hotspot score")
    assert "bug-prone" in summary


@pytest.mark.asyncio
async def test_risk_summary_unchanged_without_fix_history(setup_mcp):
    from repowise.server.mcp_server import get_risk

    result = await get_risk(["src/auth/service.py"])
    summary = result["targets"]["src/auth/service.py"]["risk_summary"]

    assert "bug fix" not in summary
    assert summary.startswith("src/auth/service.py — hotspot score")


@pytest.mark.asyncio
async def test_get_context_triage_reports_fix_history(setup_mcp, session, repo_id):
    from repowise.server.mcp_server import get_context

    # Update the file's existing row rather than inserting a second one: the
    # triage lookup expects at most one row per (repo, path).
    row = (
        await session.execute(
            select(GitMetadata).where(GitMetadata.file_path == "src/auth/service.py")
        )
    ).scalar_one()
    row.prior_defect_count = 5
    row.bug_magnet = True
    row.last_fix_at = datetime.now(UTC) - timedelta(days=3)
    await session.flush()

    result = await get_context(["src/auth/service.py"])
    t = result["targets"]["src/auth/service.py"]

    assert t["fix_history"]["fix_count"] == 5
    assert t["fix_history"]["last_fix_days_ago"] == 3
    assert t["fix_history"]["bug_magnet"] is True


@pytest.mark.asyncio
async def test_get_context_triage_omits_fix_history_when_there_is_none(setup_mcp):
    """The triage card must not grow a block of zeros on a clean file."""
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py"])
    t = result["targets"]["src/auth/service.py"]

    assert "fix_history" not in t
    assert t["hotspot"] is True  # churn bit still reported
