"""get_context ownership names the top committer all time and in the last 90 days.

The two shapes come from "who should review a change to X" questions: one where
the blame owner also leads recent commits (the card used to show no recent
owner at all), and one where the blame owner, the all-time top committer and
the recent top committer are three different people.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import update

from repowise.core.ingestion.git_indexer.file_history import _add_ownership, _Authors
from repowise.core.ingestion.git_indexer.records import _CommitRec
from repowise.core.persistence.models import GitMetadata
from repowise.server.mcp_server.tool_context.sections import _commit_leaders


def _author(name: str, commits: int, recent: int) -> dict:
    return {"name": name, "commit_count": commits, "recent_commit_count": recent}


async def _ownership(factory, **values) -> dict:
    from repowise.server.mcp_server import get_context

    async with factory() as s:
        await s.execute(update(GitMetadata).where(GitMetadata.id == "gm1").values(**values))
        await s.commit()
    result = await get_context(["src/auth/service.py"], include=["ownership"], compact=False)
    return result["targets"]["src/auth/service.py"]["ownership"]


@pytest.mark.asyncio
async def test_recent_owner_who_is_also_primary_still_gets_counts(setup_mcp, factory) -> None:
    ownership = await _ownership(
        factory,
        primary_owner_name="Nick",
        recent_owner_name="Nick",
        top_authors_json=json.dumps(
            [_author("Nick", 29, 3), _author("Sam", 19, 2), _author("Ari", 5, 2)]
        ),
    )
    assert "recent_owner" not in ownership
    assert ownership["commits"] == {"all_time": "Nick 29 of 53", "last_90d": "Nick 3 of 7"}


@pytest.mark.asyncio
async def test_blame_owner_top_committer_and_recent_owner_all_differ(setup_mcp, factory) -> None:
    ownership = await _ownership(
        factory,
        primary_owner_name="Nick",
        recent_owner_name="Sam",
        recent_owner_commit_pct=10 / 15,
        top_authors_json=json.dumps(
            [_author("Andrew", 35, 1), _author("Sam", 30, 10), _author("Nick", 25, 4)]
        ),
    )
    assert ownership["primary_owner"] == "Nick"
    assert ownership["recent_owner"] == "Sam"
    assert ownership["commits"] == {"all_time": "Andrew 35 of 90", "last_90d": "Sam 10 of 15"}


def test_quiet_file_says_no_recent_commits() -> None:
    leaders = _commit_leaders(json.dumps([_author("Bob", 13, 0)]), None)
    assert leaders == {"all_time": "Bob 13 of 13", "last_90d": "no commits"}


def test_recent_tie_goes_to_the_stored_recent_owner() -> None:
    rows = [_author("Ann", 9, 2), _author("Ben", 4, 2)]
    assert _commit_leaders(json.dumps(rows), "Ben")["last_90d"] == "Ben 2 of 4"


def test_truncated_author_list_marks_its_total() -> None:
    rows = [_author(f"dev{i}", 50 - i, 0) for i in range(50)]
    assert _commit_leaders(json.dumps(rows), None)["all_time"].endswith(" of 1275+")


def test_recent_leader_outside_the_kept_authors_gets_no_count() -> None:
    rows = [_author(f"dev{i}", 50 - i, 0) for i in range(50)]
    assert "last_90d" not in _commit_leaders(json.dumps(rows), "newcomer")


def test_rows_without_per_author_counts_add_nothing() -> None:
    # Fixtures and indexes from before per-author counts were stored.
    assert _commit_leaders(json.dumps([{"name": "Carol", "count": 3}]), None) == {}
    assert _commit_leaders("not json", None) == {}


def test_a_bot_leading_recent_commits_is_never_the_recent_leader() -> None:
    now = 1_700_000_000
    bot = ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
    dev = ("Dev", "dev@example.com")
    commits = [
        _CommitRec(f"{i:040d}", n, e, now - i, False, "change", added=1, deleted=1)
        for i, (n, e) in enumerate([bot, bot, bot, dev])
    ]
    meta: dict = {}
    _add_ownership(meta, _Authors.tally(commits, now - 1000))
    assert _commit_leaders(meta["top_authors_json"], meta["recent_owner_name"]) == {
        "all_time": "Dev 1 of 1",
        "last_90d": "Dev 1 of 1",
    }
