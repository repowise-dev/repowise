"""Ride-along awards on the /stats/highlights activity scan.

``biggest_commit`` must skip the repo's very first commit (every initial
import would win otherwise) and ``longest_streak`` counts consecutive UTC
days with at least one commit. ``most_central_file`` names the most-imported
file that is not a test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from repowise.core.persistence.crud import get_repository, upsert_git_commits_bulk
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphMetric, GraphNode
from repowise.server.routers.stats import _commit_pass, _records
from tests.unit.server.conftest import create_test_repo

_DAY = 86400


def _commit(sha: str, ts: int, added: int, deleted: int = 0, files: int = 1) -> dict:
    return {
        "sha": sha,
        "author_name": "Jane Doe",
        "author_email": "jane@company.com",
        "committed_at": datetime.fromtimestamp(ts, tz=UTC),
        "subject": f"commit {sha}",
        "lines_added": added,
        "lines_deleted": deleted,
        "files_changed": files,
        "dirs_changed": 1,
        "subsystems_changed": 1,
        "entropy": 0.1,
        "is_fix": False,
        "change_risk_score": 1.0,
        "change_risk_level": "low",
    }


@pytest.mark.asyncio
async def test_biggest_commit_skips_initial_and_streak_counts_days(
    client: AsyncClient, app
) -> None:
    repo = await create_test_repo(client)
    rows = [
        # Initial commit is the largest by churn but must not win the award.
        _commit("a1", 1000, added=50_000, files=300),
        # Days 2-4 form a 3-day streak; day 2's commit is the rightful winner.
        _commit("b1", 1000 + _DAY, added=900, deleted=100, files=12),
        _commit("c1", 1000 + 2 * _DAY, added=10),
        _commit("d1", 1000 + 3 * _DAY, added=10),
        # A gap, then a lone day — streak stays 4 (day 1 chains into days 2-4).
        _commit("e1", 1000 + 10 * _DAY, added=10),
    ]
    async with get_session(app.state.session_factory) as session:
        await upsert_git_commits_bulk(session, repo["id"], rows)

    async with get_session(app.state.session_factory) as session:
        repo_row = await get_repository(session, repo["id"])
        activity = await _commit_pass(session, repo["id"], repo_row)

    biggest = activity["biggest_commit"]
    assert biggest is not None
    assert biggest["sha"] == "b1"
    assert biggest["lines_changed"] == 1000
    assert biggest["files_changed"] == 12

    streak = activity["rhythm"]["longest_streak"]
    assert streak is not None
    assert streak["days"] == 4


@pytest.mark.asyncio
async def test_most_central_file_skips_tests_without_false_positives(
    client: AsyncClient, app
) -> None:
    """The award goes to the top real source file (#1103).

    Two ways to get this wrong, one on each side. The unanchored regex this
    used to re-check with read ``src/latest/api.py`` as a test and handed the
    award to the runner-up. Trusting ``is_test`` alone would hand it to
    ``tests/conftest.py``, which tops fan-in in any repo sharing fixtures
    widely and whose stored flag is only as fresh as its last traversal.
    """
    repo = await create_test_repo(client)
    nodes = [
        # Stale flag: stamped before conftest counted as test material.
        ("tests/conftest.py", False, 700),
        ("src/latest/api.py", False, 90),  # not a test, whatever the name reads like
        ("src/util.py", False, 40),
    ]
    async with get_session(app.state.session_factory) as session:
        for path, is_test, in_degree in nodes:
            session.add(
                GraphNode(
                    repository_id=repo["id"],
                    node_id=path,
                    node_type="file",
                    is_test=is_test,
                    pagerank=0.1,
                )
            )
            session.add(
                GraphMetric(
                    repository_id=repo["id"],
                    node_id=path,
                    in_degree=in_degree,
                    pagerank=0.1,
                )
            )
        await session.commit()

    async with get_session(app.state.session_factory) as session:
        out = await _records(session, repo["id"], metrics=[], all_meta=[])

    assert out["most_central_file"]["path"] == "src/latest/api.py"
    assert out["most_central_file"]["import_count"] == 90
