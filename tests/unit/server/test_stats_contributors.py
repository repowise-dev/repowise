"""Contributor-count dedup on the /stats/highlights activity payload.

The "By the Numbers" contributor count keys on commit author identity; GitHub
noreply variants and a person's same-name real+noreply emails must fold to one
person so the headline count isn't inflated.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from repowise.core.persistence.crud import get_repository, upsert_git_commits_bulk
from repowise.core.persistence.database import get_session
from repowise.server.routers.stats import _activity
from tests.unit.server.conftest import create_test_repo


def _commit(sha: str, name: str, email: str, ts: int) -> dict:
    return {
        "sha": sha,
        "author_name": name,
        "author_email": email,
        "committed_at": datetime.fromtimestamp(ts, tz=UTC),
        "subject": f"commit {sha}",
        "lines_added": 5,
        "lines_deleted": 1,
        "files_changed": 1,
        "dirs_changed": 1,
        "subsystems_changed": 1,
        "entropy": 0.1,
        "is_fix": False,
        "change_risk_score": 1.0,
        "change_risk_level": "low",
    }


@pytest.mark.asyncio
async def test_contributor_count_folds_noreply_and_same_name(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    rows = [
        # Jane: real email + two noreply variants (numeric id changed) — one person.
        _commit("a1", "Jane Doe", "jane@company.com", 1000),
        _commit("a2", "Jane Doe", "12345+jane@users.noreply.github.com", 1100),
        _commit("a3", "Jane Doe", "999+jane@users.noreply.github.com", 1200),
        # Bob: a genuinely separate contributor.
        _commit("b1", "Bob", "bob@company.com", 1300),
    ]
    async with get_session(app.state.session_factory) as session:
        await upsert_git_commits_bulk(session, repo["id"], rows)

    async with get_session(app.state.session_factory) as session:
        repo_row = await get_repository(session, repo["id"])
        activity = await _activity(session, repo["id"], repo_row)

    # No whole-history totals stored on the repo, so the count folds the
    # bounded sample's author identities (Jane + Bob, not 4).
    assert activity["total_commits"] == 4
    assert activity["contributor_count"] == 2


@pytest.mark.asyncio
async def test_activity_prefers_whole_history_totals(client: AsyncClient, app) -> None:
    """When the repo carries index-time totals, the headline reads those, not
    the bounded ``git_commits`` sample (issue #730)."""
    repo = await create_test_repo(client)
    # Two sampled commits from one author, ~1 day apart.
    rows = [
        _commit("a1", "Jane Doe", "jane@company.com", 1_600_000_000),
        _commit("a2", "Jane Doe", "jane@company.com", 1_600_086_400),
    ]
    async with get_session(app.state.session_factory) as session:
        await upsert_git_commits_bulk(session, repo["id"], rows)

    # Stamp true whole-history values on the repo, far larger than the sample.
    async with get_session(app.state.session_factory) as session:
        repo_row = await get_repository(session, repo["id"])
        repo_row.total_commit_count = 5000
        repo_row.total_contributor_count = 42
        repo_row.first_commit_at = datetime.fromtimestamp(1_300_000_000, tz=UTC)
        repo_row.first_commit_author = "Ada Lovelace"
        await session.flush()

    async with get_session(app.state.session_factory) as session:
        repo_row = await get_repository(session, repo["id"])
        activity = await _activity(session, repo["id"], repo_row)

    assert activity["total_commits"] == 5000
    assert activity["contributor_count"] == 42
    assert activity["first_commit_author"] == "Ada Lovelace"
    # Age runs from the true first commit to the latest sampled commit, so it is
    # far larger than the ~1 day the sample alone would show.
    assert activity["age_days"] > 3000
