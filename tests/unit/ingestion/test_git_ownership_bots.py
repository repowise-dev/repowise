"""Bot accounts are neither owners nor contributors; agents and churn are untouched."""

from __future__ import annotations

import json

from repowise.core.ingestion.git_indexer.file_history import _add_ownership, _Authors
from repowise.core.ingestion.git_indexer.records import _CommitRec

_NOW = 1_700_000_000
_BOT = ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")
_DEV = ("Dev", "dev@example.com")
_AGENT = ("Claude", "noreply@anthropic.com")


def _commits(*authors: tuple[str, str]) -> list[_CommitRec]:
    return [
        _CommitRec(f"{i:040d}", n, e, _NOW - i, False, "change", added=1, deleted=1)
        for i, (n, e) in enumerate(authors)
    ]


def _own(*authors: tuple[str, str]) -> dict:
    meta: dict = {}
    _add_ownership(meta, _Authors.tally(_commits(*authors), _NOW - 1000))
    return meta


def test_bot_majority_file_owner_is_the_human() -> None:
    meta = _own(_BOT, _BOT, _BOT, _DEV)
    assert meta["primary_owner_name"] == "Dev"
    assert meta["primary_owner_commit_pct"] == 1.0
    assert meta["contributor_count"] == 1
    assert meta["bus_factor"] == 1
    assert meta["recent_owner_name"] == "Dev"
    assert [a["name"] for a in json.loads(meta["top_authors_json"])] == ["Dev"]


def test_bot_only_file_has_no_owner() -> None:
    meta = _own(_BOT, _BOT)
    assert meta["contributor_count"] == 0
    assert "primary_owner_name" not in meta
    assert "recent_owner_name" not in meta
    assert "bus_factor" not in meta
    assert json.loads(meta["top_authors_json"]) == []


def test_agent_majority_file_keeps_agent_owner() -> None:
    meta = _own(_AGENT, _AGENT, _AGENT, _DEV)
    assert meta["primary_owner_name"] == "Claude"
    assert meta["contributor_count"] == 2
